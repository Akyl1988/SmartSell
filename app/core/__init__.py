# app/core/__init__.py
from __future__ import annotations

"""
SmartSell3 Core Package Initializer (enterprise-grade)

Возможности:
- Единая инициализация ядра FastAPI: логи, исключения, middleware, health endpoints.
- /livez — liveness probe (лёгкий), /readyz — readiness probe (DB и критичные ENV), /metrics — Prometheus.
- Интеграция с Prometheus (prometheus_client) и StatsD (если установлен statsd).
- Авто-миграции Alembic при старте, если RUN_MIGRATIONS_ON_START=1.
- Логирование correlation/request id и trace_id/span_id (OpenTelemetry, если доступен).
- Реэкспорт ключевых сущностей ядра (settings, db, логгер и пр.).

Подключение:
    from fastapi import FastAPI
    from app.core import init_core

    app = FastAPI(title="SmartSell3")
    init_core(app)  # всё необходимое за один вызов
"""

import os
import time
import uuid
from typing import Any, Optional

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse, PlainTextResponse

# --- Settings ---
try:
    from app.core.config import get_settings, settings  # type: ignore
except Exception as e:  # pragma: no cover
    raise RuntimeError("Failed to import settings from app.core.config") from e

# --- Logging: structured logger + audit ---
try:
    from app.core.logging import (  # type: ignore
        audit_logger,
        bind_context,
        configure_logging,
        get_logger,
    )
except Exception as e:  # pragma: no cover
    raise RuntimeError("Failed to import logging utilities from app.core.logging") from e

# --- Exceptions registration ---
try:
    from app.core.exceptions import register_exception_handlers  # type: ignore
except Exception as e:  # pragma: no cover
    raise RuntimeError("Failed to import exception handlers from app.core.exceptions") from e

# --- Database (sync/async) — поддерживаем оба пути: database.py предпочтительно, db.py как fallback ---
_DB_IMPORT_ERROR: Optional[Exception] = None
engine = None
AsyncSession = None
async_session_maker = None
create_async_engine = None
SessionLocal = None
Base = None

try:  # предпочтительно
    from app.core.database import AsyncSession as _AsyncSession
    from app.core.database import Base as _Base
    from app.core.database import SessionLocal as _SessionLocal
    from app.core.database import async_session_maker as _async_session_maker
    from app.core.database import create_async_engine as _create_async_engine
    from app.core.database import engine as _engine  # type: ignore

    engine = _engine
    AsyncSession = _AsyncSession
    async_session_maker = _async_session_maker
    create_async_engine = _create_async_engine
    SessionLocal = _SessionLocal
    Base = _Base
except Exception:
    try:
        from app.core.db import AsyncSession as _AsyncSession
        from app.core.db import Base as _Base
        from app.core.db import SessionLocal as _SessionLocal
        from app.core.db import async_session_maker as _async_session_maker
        from app.core.db import create_async_engine as _create_async_engine
        from app.core.db import engine as _engine  # type: ignore

        engine = _engine
        AsyncSession = _AsyncSession
        async_session_maker = _async_session_maker
        create_async_engine = _create_async_engine
        SessionLocal = _SessionLocal
        Base = _Base
    except Exception as e2:
        _DB_IMPORT_ERROR = e2  # не валим импорт пакета; init_core предупредит в логе

# --- OpenTelemetry (best-effort) ---
try:
    from opentelemetry.trace import get_current_span  # type: ignore

    _HAS_OTEL = True
except Exception:
    _HAS_OTEL = False

# --- Prometheus (best-effort) ---
try:
    from prometheus_client import (  # type: ignore
        CONTENT_TYPE_LATEST,
        CollectorRegistry,
        Counter,
        Gauge,
        Histogram,
        generate_latest,
    )

    _HAS_PROM = True
except Exception:
    _HAS_PROM = False

# --- StatsD (best-effort) ---
try:
    from statsd import StatsClient  # type: ignore

    _HAS_STATSD = True
except Exception:
    _HAS_STATSD = False

__version__: str = getattr(settings, "VERSION", "0.0.0")


def build_info() -> dict[str, Any]:
    """Структурированная информация о сборке/окружении (для /health, логов)."""
    try:
        info = settings.build_info  # type: ignore[attr-defined]
    except Exception:
        info = {"project": getattr(settings, "PROJECT_NAME", "SmartSell3"), "version": __version__}
    return {
        **info,
        "environment": getattr(settings, "ENVIRONMENT", "development"),
    }


# ------------------------------------------------------------------------------
# Metrics (Prometheus/StatsD)
# ------------------------------------------------------------------------------
_PROM_REGISTRY = None
_HTTP_REQ_COUNT = None
_HTTP_REQ_LATENCY = None
_READY_GAUGE = None
_statd_client = None


def _init_metrics() -> None:
    global _PROM_REGISTRY, _HTTP_REQ_COUNT, _HTTP_REQ_LATENCY, _READY_GAUGE, _statd_client
    log = get_logger("metrics-init")

    if _HAS_PROM:
        _PROM_REGISTRY = CollectorRegistry()
        _HTTP_REQ_COUNT = Counter(
            "http_requests_total",
            "Total HTTP requests",
            ["method", "path", "status"],
            registry=_PROM_REGISTRY,
        )
        _HTTP_REQ_LATENCY = Histogram(
            "http_request_duration_seconds",
            "HTTP request latency (seconds)",
            ["method", "path"],
            registry=_PROM_REGISTRY,
        )
        _READY_GAUGE = Gauge(
            "smartsell_ready", "Readiness status (1=ready,0=not_ready)", registry=_PROM_REGISTRY
        )
        _READY_GAUGE.set(0)
        log.info("Prometheus metrics initialized")
    else:
        log.warning("prometheus_client not installed; /metrics will be minimal")

    if _HAS_STATSD:
        host = os.getenv("STATSD_HOST", "127.0.0.1")
        port = int(os.getenv("STATSD_PORT", "8125"))
        prefix = os.getenv("STATSD_PREFIX", "smartsell")
        try:
            _statd_client = StatsClient(host=host, port=port, prefix=prefix)
            log.info("StatsD client initialized", host=host, port=port, prefix=prefix)
        except Exception as e:
            log.warning("StatsD init failed", error=str(e))


def _observe_request_metrics(method: str, path: str, status_code: int, duration: float) -> None:
    try:
        if _HAS_PROM and _HTTP_REQ_COUNT and _HTTP_REQ_LATENCY:
            _HTTP_REQ_COUNT.labels(method=method, path=path, status=str(status_code)).inc()
            _HTTP_REQ_LATENCY.labels(method=method, path=path).observe(duration)
        if _HAS_STATSD and _statd_client:
            _statd_client.incr(f"http.requests_total.{method}.{status_code}")
            _statd_client.timing(f"http.request_duration_ms.{method}", int(duration * 1000))
    except Exception:
        pass


# ------------------------------------------------------------------------------
# Middleware helpers (FastAPI/Starlette)
# ------------------------------------------------------------------------------
def _add_trusted_hosts_middleware(app: FastAPI) -> None:
    try:
        from starlette.middleware.trustedhost import TrustedHostMiddleware

        allowed = getattr(settings, "ALLOWED_HOSTS", ["*"]) or ["*"]
        if allowed != ["*"]:
            app.add_middleware(TrustedHostMiddleware, allowed_hosts=allowed)
    except Exception:
        pass


def _add_cors_middleware(app: FastAPI) -> None:
    try:
        from fastapi.middleware.cors import CORSMiddleware

        cors = getattr(settings, "cors_config", None)
        if isinstance(cors, dict):
            app.add_middleware(
                CORSMiddleware,
                allow_origins=cors.get("allow_origins", ["*"]),
                allow_credentials=cors.get("allow_credentials", True),
                allow_methods=cors.get("allow_methods", ["*"]),
                allow_headers=cors.get("allow_headers", ["*"]),
                expose_headers=cors.get("expose_headers", ["X-Request-ID", "Trace-Id", "Span-Id"]),
                max_age=cors.get("max_age", 600),
            )
        else:
            app.add_middleware(
                CORSMiddleware,
                allow_origins=["*"],
                allow_credentials=True,
                allow_methods=["*"],
                allow_headers=["*"],
                expose_headers=["X-Request-ID", "Trace-Id", "Span-Id"],
                max_age=600,
            )
    except Exception:
        pass


def _add_request_id_and_tracing_middleware(app: FastAPI) -> None:
    """
    Correlation-id и OpenTelemetry trace/span id биндим в лог-контекст и заголовки ответа.
    """
    from starlette.middleware.base import BaseHTTPMiddleware

    class RequestIDTracingMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request: Request, call_next):
            req_id = (
                request.headers.get("x-request-id")
                or request.headers.get("x-correlation-id")
                or str(uuid.uuid4())
            )

            trace_id_hex = ""
            span_id_hex = ""
            if _HAS_OTEL:
                try:
                    span = get_current_span()
                    if span and span.get_span_context():
                        ctx = span.get_span_context()
                        trace_id_hex = format(ctx.trace_id, "032x")
                        span_id_hex = format(ctx.span_id, "016x")
                except Exception:
                    pass

            t0 = time.perf_counter()
            try:
                with bind_context(request_id=req_id, trace_id=trace_id_hex, span_id=span_id_hex):
                    response: Response = await call_next(request)
            except Exception:
                response: Response = await call_next(request)
            dt = time.perf_counter() - t0

            # метрики
            try:
                _observe_request_metrics(
                    request.method, request.url.path, getattr(response, "status_code", 500), dt
                )
            except Exception:
                pass

            response.headers.setdefault("X-Request-ID", req_id)
            if trace_id_hex:
                response.headers.setdefault("Trace-Id", trace_id_hex)
            if span_id_hex:
                response.headers.setdefault("Span-Id", span_id_hex)

            return response

    app.add_middleware(RequestIDTracingMiddleware)


def _add_security_headers_middleware(app: FastAPI) -> None:
    from starlette.middleware.base import BaseHTTPMiddleware

    class SecurityHeadersMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request: Request, call_next):
            response = await call_next(request)
            response.headers.setdefault("X-Content-Type-Options", "nosniff")
            response.headers.setdefault("X-Frame-Options", "DENY")
            response.headers.setdefault("Referrer-Policy", "no-referrer-when-downgrade")
            return response

    app.add_middleware(SecurityHeadersMiddleware)


# ------------------------------------------------------------------------------
# Health/self-checks
# ------------------------------------------------------------------------------
def _critical_env_warnings() -> list[str]:
    """
    Проверка критичных ENV/настроек: предупреждаем в логах, но сервис не валим.
    """
    issues: list[str] = []
    env = str(getattr(settings, "ENVIRONMENT", "development")).lower()

    # DB URL в проде
    if env == "production":
        db_url = getattr(settings, "DATABASE_URL", None)
        if not db_url:
            issues.append("DATABASE_URL is missing in production")

    # SECRET_KEY
    sk = getattr(settings, "SECRET_KEY", "")
    if not sk or sk.strip().lower() in {"changeme", "secret", "password"}:
        issues.append("Insecure SECRET_KEY")

    # Sentry (не критично, но полезно для продакшна)
    if env == "production" and not getattr(settings, "SENTRY_DSN", None):
        issues.append("SENTRY_DSN not set (observability)")

    # Prometheus/StatsD наличие — не обязательны, поэтому только инфо.
    return issues


def core_health_check() -> dict[str, Any]:
    """
    Лёгкая самопроверка ядра.
    """
    errors: list[str] = []

    # Настройки
    issues = _critical_env_warnings()
    errors.extend(issues)

    # БД импорт
    if _DB_IMPORT_ERROR:
        errors.append(f"DB import error: {_DB_IMPORT_ERROR!r}")

    ok = not errors
    return {
        "ok": ok,
        "errors": errors,
        "build": build_info(),
    }


def _ready_check_db() -> tuple[bool, Optional[str]]:
    """
    Минимальный readiness-чек БД: SELECT 1 (если engine доступен).
    """
    if engine is None:
        return False, "DB engine not initialized"
    try:
        with engine.connect() as conn:  # sync engine из core.db/database
            conn.execute("SELECT 1")
        return True, None
    except Exception as e:
        return False, str(e)


# ------------------------------------------------------------------------------
# Alembic migrations (on startup)
# ------------------------------------------------------------------------------
def _run_alembic_migrations_if_needed() -> None:
    if os.getenv("RUN_MIGRATIONS_ON_START", "0") not in ("1", "true", "True"):
        return
    log = get_logger("alembic")
    try:
        # Локальный импорт, чтобы не тянуть Alembic как обязательную зав-ть в рантайме
        from alembic import command  # type: ignore
        from alembic.config import Config  # type: ignore

        # Пути конфигурации можно вынести в настройки при необходимости
        alembic_cfg_path = os.getenv("ALEMBIC_CONFIG", "alembic.ini")
        cfg = Config(alembic_cfg_path)
        # Пример: можно подставить URL из settings, если alembic.ini использует переменную
        if getattr(settings, "DATABASE_URL", None):
            cfg.set_main_option("sqlalchemy.url", getattr(settings, "DATABASE_URL"))
        log.info("Running Alembic migrations...", config=alembic_cfg_path)
        command.upgrade(cfg, "head")
        log.info("Alembic migrations complete")
    except Exception as e:
        # В проде разумно решать: фаталить или продолжать. Здесь — предупреждаем и продолжаем.
        log.error("Alembic migrations failed", exc_info=e)


# ------------------------------------------------------------------------------
# Endpoints (/livez, /readyz, /metrics)
# ------------------------------------------------------------------------------
def _register_health_endpoints(app: FastAPI) -> None:
    log = get_logger("health-endpoints")

    @app.get("/livez", include_in_schema=False)
    async def livez() -> Response:
        """
        Liveness probe — отвечает всегда быстро (без сетевых вызовов).
        """
        return PlainTextResponse("ok", status_code=200)

    @app.get("/readyz", include_in_schema=False)
    async def readyz() -> Response:
        """
        Readiness probe — проверка внутренних зависимостей (БД и критичные ENV).
        """
        core = core_health_check()
        db_ok, db_err = _ready_check_db()
        ready = core["ok"] and db_ok
        if _HAS_PROM and _READY_GAUGE is not None:
            _READY_GAUGE.set(1 if ready else 0)
        body = {
            "ok": ready,
            "build": core["build"],
            "core_errors": core["errors"],
            "db_ok": db_ok,
            "db_error": db_err,
        }
        return JSONResponse(body, status_code=200 if ready else 503)

    @app.get("/metrics", include_in_schema=False)
    async def metrics() -> Response:
        """
        Prometheus endpoint. Если prometheus_client не установлен —
        возвращает минимальный текст «metrics disabled».
        """
        if _HAS_PROM and _PROM_REGISTRY is not None:
            data = generate_latest(_PROM_REGISTRY)  # type: ignore
            return Response(content=data, media_type=CONTENT_TYPE_LATEST)
        return PlainTextResponse(
            "# metrics disabled (prometheus_client not installed)\n", status_code=200
        )

    log.info("Health endpoints registered", endpoints=["/livez", "/readyz", "/metrics"])


# ------------------------------------------------------------------------------
# Unified initializer
# ------------------------------------------------------------------------------
def init_core(app: FastAPI) -> None:
    """
    Единая инициализация ядра FastAPI:
      - Логирование (структурированное)
      - Регистрация хендлеров исключений
      - Middleware: TrustedHosts, CORS, RequestID+Tracing, SecurityHeaders
      - Метрики Prometheus/StatsD
      - Health endpoints: /livez, /readyz, /metrics
      - Старт-хук: Alembic миграции при RUN_MIGRATIONS_ON_START=1
      - Лог build-info, предупреждений по критичным ENV
    """
    # Логирование
    try:
        configure_logging()
    except Exception:
        pass

    # Метрики
    _init_metrics()

    # Исключения
    try:
        register_exception_handlers(app)
    except Exception as e:
        log = get_logger("init")
        log.error("Failed to register exception handlers", exc_info=e)

    # Middleware
    _add_trusted_hosts_middleware(app)
    _add_cors_middleware(app)
    _add_request_id_and_tracing_middleware(app)
    _add_security_headers_middleware(app)

    # Health endpoints
    _register_health_endpoints(app)

    # Startup hook: Alembic (best-effort)
    @app.on_event("startup")
    async def _on_startup() -> None:
        # предупреждения по критичным ENV
        issues = _critical_env_warnings()
        log = get_logger("startup")
        if issues:
            log.warning("Critical ENV issues", issues=issues)
        # alembic
        _run_alembic_migrations_if_needed()
        # готовность ядра для /readyz прометheus-метрики
        try:
            if _HAS_PROM and _READY_GAUGE is not None:
                # после старта ещё не проверяли DB — консервативно 0; поднимется при первом /readyz
                _READY_GAUGE.set(0)
        except Exception:
            pass

    # Стартовый лог
    log = get_logger("startup")
    try:
        info = build_info()
        core = core_health_check()
        log.info("Core initialized", build=info, core_ok=core["ok"], core_errors=core["errors"])
    except Exception as e:
        log.warning("Core initialized (with issues)", error=str(e))


# ------------------------------------------------------------------------------
# Public API of the package
# ------------------------------------------------------------------------------
__all__ = [
    # settings
    "settings",
    "get_settings",
    "__version__",
    "build_info",
    "core_health_check",
    # logging
    "configure_logging",
    "get_logger",
    "audit_logger",
    "bind_context",
    # exceptions
    "register_exception_handlers",
    # db (may be None if not available at import time)
    "engine",
    "AsyncSession",
    "async_session_maker",
    "create_async_engine",
    "SessionLocal",
    "Base",
    # init
    "init_core",
]
