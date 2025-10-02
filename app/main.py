# app/main.py
from __future__ import annotations

import os
import sys
import asyncio
import logging
import smtplib
import socket
import time
import uuid
import importlib
import pkgutil
from collections import deque
from contextlib import asynccontextmanager
from typing import Callable, Awaitable, Optional, Dict, Any, Tuple, List
from contextvars import ContextVar

from fastapi import FastAPI, Request, Response, HTTPException, Body, Path, APIRouter, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse, RedirectResponse

# ✅ агрегатор v1-роутеров (wallet/payments/campaigns/users/auth/products)
from app.api.routes import mount_v1

try:
    from starlette.staticfiles import StaticFiles
except Exception:  # pragma: no cover
    StaticFiles = None  # type: ignore

try:
    from starlette.middleware.trustedhost import TrustedHostMiddleware
except Exception:  # pragma: no cover
    TrustedHostMiddleware = None  # type: ignore

try:
    from starlette.middleware.sessions import SessionMiddleware
except Exception:  # pragma: no cover
    SessionMiddleware = None  # type: ignore

from app.core.config import settings

# ======================================================================================
# LOGGER
# ======================================================================================
logger = logging.getLogger(__name__)
if not logger.handlers:
    log_level = os.getenv("LOG_LEVEL", "INFO").upper()
    logging.basicConfig(level=log_level, format="%(asctime)s %(levelname)s [%(name)s] %(message)s")

_request_id_var: ContextVar[str] = ContextVar("request_id", default="-")

class _RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            setattr(record, "request_id", _request_id_var.get("-"))
        except Exception:
            setattr(record, "request_id", "-")
        return True

logging.getLogger().addFilter(_RequestIdFilter())

# ======================================================================================
# GLOBAL STATE
# ======================================================================================
_GLOBAL: Dict[str, Any] = {
    "redis": None,
    "db_engine": None,
    "httpx": None,
    "celery": None,
    "scheduler_started": False,
}
_START_TS = time.time()

# ======================================================================================
# Prometheus / Metrics (optional)
# ======================================================================================
PROM_AVAILABLE = False
STARLETTE_EXPORTER_AVAILABLE = False
prom_objs: Dict[str, Any] = {}

try:
    from starlette_exporter import PrometheusMiddleware, handle_metrics  # type: ignore

    STARLETTE_EXPORTER_AVAILABLE = True
    logger.info("starlette_exporter is available — will attach Prometheus middleware")
except Exception:
    try:
        from prometheus_client import (  # type: ignore
            Counter,
            Histogram,
            Gauge,
            CollectorRegistry,
            CONTENT_TYPE_LATEST,
            generate_latest,
        )

        PROM_AVAILABLE = True
        prom_objs["registry"] = CollectorRegistry()
        prom_objs["requests_total"] = Counter(
            "http_requests_total",
            "Total HTTP requests",
            ["method", "path", "status_code"],
            registry=prom_objs["registry"],
        )
        prom_objs["request_latency_seconds"] = Histogram(
            "http_request_latency_seconds",
            "Latency of HTTP requests in seconds",
            ["method", "path"],
            registry=prom_objs["registry"],
        )
        prom_objs["health_status"] = Gauge("app_health_ok", "1 if health ok else 0", registry=prom_objs["registry"])
        prom_objs["postgres_latency_seconds"] = Histogram(
            "postgres_latency_seconds", "Latency of Postgres probe in seconds", registry=prom_objs["registry"]
        )
        prom_objs["postgres_is_primary"] = Gauge("postgres_is_primary", "1 if Primary, 0 if Standby or unknown", registry=prom_objs["registry"])
        prom_objs["postgres_is_readonly"] = Gauge("postgres_is_readonly", "1 if default_transaction_read_only=on, else 0", registry=prom_objs["registry"])
        prom_objs["postgres_active_connections"] = Gauge("postgres_active_connections", "Number of active connections (pg_stat_database sum)", registry=prom_objs["registry"])
        prom_objs["postgres_replication_lag_bytes"] = Gauge("postgres_replication_lag_bytes", "Max replication lag in bytes if available", registry=prom_objs["registry"])
        prom_objs["celery_workers_up"] = Gauge("celery_workers_up", "Number of responding Celery workers", registry=prom_objs["registry"])
        prom_objs["celery_ping_latency_seconds"] = Histogram("celery_ping_latency_seconds", "Latency of Celery control.ping() in seconds", registry=prom_objs["registry"])
        prom_objs["external_api_latency_seconds"] = Histogram(
            "external_api_latency_seconds", "Latency of external API checks in seconds", ["name"], registry=prom_objs["registry"]
        )
        prom_objs["external_api_up"] = Gauge("external_api_up", "1 if external API healthy else 0", ["name"], registry=prom_objs["registry"])
        logger.info("prometheus_client is available — metrics will be exposed at /metrics")
    except Exception:
        logger.info("Prometheus libs not installed — /metrics will return a basic text")

# ======================================================================================
# OpenTelemetry (optional)
# ======================================================================================
OTEL_AVAILABLE = False
try:
    from opentelemetry import trace  # type: ignore
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor  # type: ignore

    try:
        from opentelemetry.instrumentation.asgi import OpenTelemetryMiddleware  # type: ignore
        OTEL_ASGI_AVAILABLE = True
    except Exception:
        OTEL_ASGI_AVAILABLE = False
    OTEL_AVAILABLE = True
    logger.info("OpenTelemetry instrumentation is available — tracing can be enabled")
except Exception:
    OTEL_AVAILABLE = False
    OTEL_ASGI_AVAILABLE = False

# ======================================================================================
# Models import helper (once)
# ======================================================================================
def _import_models_once() -> None:
    try:
        import app.models as models_pkg  # noqa: F401
    except Exception:
        return
    try:
        pkg_name = "app.models"
        pkg = sys.modules.get(pkg_name)
        if not pkg or not hasattr(pkg, "__path__"):
            return
        for m in pkgutil.walk_packages(pkg.__path__, pkg_name + "."):
            name = m.name
            if name in sys.modules:
                continue
            try:
                importlib.import_module(name)
            except Exception as e:
                logger.debug("Model module %s import skipped: %s", name, e)
    except Exception as e:
        logger.debug("Models auto-import failed: %s", e)

# ======================================================================================
# Helpers
# ======================================================================================
def _is_postgres_url(url: str | None) -> bool:
    if not url:
        return False
    u = (url or "").lower()
    return u.startswith("postgres://") or u.startswith("postgresql://")

def _env_last_deploy_time() -> str:
    for k in ("LAST_DEPLOY_AT", "LAST_DEPLOY_TIME", "DEPLOYED_AT", "DEPLOY_TIME"):
        v = os.getenv(k)
        if v:
            return v
    return ""

def _parse_trusted_hosts() -> Optional[List[str]]:
    raw = os.getenv("TRUSTED_HOSTS", "")
    if not raw:
        return None
    return [h.strip() for h in raw.split(",") if h.strip()]

def _uptime_seconds() -> int:
    try:
        return int(time.time() - _START_TS)
    except Exception:
        return 0

def _server_timing_value(ms: int) -> str:
    try:
        return f'app;desc="handler";dur={float(ms):.1f}'
    except Exception:
        return "app;dur=0"

_SECRET_KEYS = ("SECRET", "PASSWORD", "TOKEN", "KEY", "PASS", "PRIVATE", "CREDENTIAL", "AUTH")

def _redact(value: Any) -> Any:
    try:
        if value is None:
            return None
        s = str(value)
        if not s:
            return s
        if len(s) <= 6:
            return "***"
        return s[:2] + "…" + s[-2:]
    except Exception:
        return "***"

def _redact_dict(d: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for k, v in d.items():
        if any(part in str(k).upper() for part in _SECRET_KEYS):
            out[k] = _redact(v)
        else:
            out[k] = v
    return out

def _has_path_prefix(app: FastAPI, prefix: str) -> bool:
    """Проверяет, что в приложении уже есть хотя бы один маршрут на указанный префикс."""
    try:
        for r in app.router.routes:
            p = getattr(r, "path", None) or getattr(r, "path_format", None)
            if isinstance(p, str) and p.startswith(prefix):
                return True
    except Exception:
        pass
    return False

# ======================================================================================
# PostgreSQL deep probe
# ======================================================================================
async def _pg_probe(sync_url: str, timeout: float = 3.0) -> tuple[bool, str, dict]:
    info: Dict[str, Any] = {}
    if not _is_postgres_url(sync_url):
        return False, "not_postgres_url", info

    t0 = time.perf_counter()
    try:
        from sqlalchemy import create_engine, text as sql_text  # type: ignore

        engine = _GLOBAL.get("db_engine")
        if engine is None:
            engine = create_engine(sync_url, pool_pre_ping=True, pool_size=1, max_overflow=0, pool_timeout=timeout)
            _GLOBAL["db_engine"] = engine

        def _run_queries() -> dict:
            out: Dict[str, Any] = {}
            with engine.connect() as conn:
                out["server_version"] = conn.execute(sql_text("SHOW server_version")).scalar()  # type: ignore
                out["current_database"] = conn.execute(sql_text("SELECT current_database()")).scalar()  # type: ignore
                out["timezone"] = conn.execute(sql_text("SHOW TimeZone")).scalar()  # type: ignore
                out["default_transaction_read_only"] = conn.execute(sql_text("SHOW default_transaction_read_only")).scalar()  # type: ignore
                try:
                    is_recovery = conn.execute(sql_text("SELECT pg_is_in_recovery()")).scalar()  # type: ignore
                except Exception:
                    is_recovery = None
                out["is_standby"] = bool(is_recovery) if is_recovery is not None else None
                out["is_primary"] = (False if out.get("is_standby") else True if out.get("is_standby") is not None else None)

                try:
                    active_conns = conn.execute(sql_text("SELECT COALESCE(SUM(numbackends),0) FROM pg_stat_database")).scalar()  # type: ignore
                except Exception:
                    active_conns = None
                out["active_connections"] = int(active_conns or 0)

                try:
                    exts = conn.execute(sql_text("SELECT extname FROM pg_extension")).fetchall()  # type: ignore
                    out["extensions"] = [r[0] for r in exts] if exts else []
                except Exception:
                    out["extensions"] = None

                lag_bytes = None
                try:
                    if out.get("is_standby"):
                        r = conn.execute(
                            sql_text(
                                """
                                SELECT
                                  CASE
                                    WHEN pg_last_wal_receive_lsn() IS NULL OR pg_last_wal_replay_lsn() IS NULL
                                    THEN NULL
                                    ELSE pg_wal_lsn_diff(pg_last_wal_receive_lsn(), pg_last_wal_replay_lsn())
                                  END AS lag_bytes
                                """
                            )
                        ).scalar()  # type: ignore
                        lag_bytes = int(r) if r is not None else None
                except Exception:
                    lag_bytes = None
                out["replication_lag_bytes"] = lag_bytes
            return out

        result = await asyncio.wait_for(asyncio.to_thread(_run_queries), timeout=timeout)
        info.update(result)

        duration = time.perf_counter() - t0
        info["probe_latency_ms"] = int(duration * 1000)

        if PROM_AVAILABLE:
            try:
                prom_objs["postgres_latency_seconds"].observe(duration)
                if isinstance(info.get("is_primary"), bool):
                    prom_objs["postgres_is_primary"].set(1 if info["is_primary"] else 0)
                if isinstance(info.get("default_transaction_read_only"), str):
                    prom_objs["postgres_is_readonly"].set(
                        1 if info["default_transaction_read_only"].lower() in ("on", "true", "1") else 0
                    )
                if isinstance(info.get("active_connections"), int):
                    prom_objs["postgres_active_connections"].set(info["active_connections"])
                if isinstance(info.get("replication_lag_bytes"), int):
                    prom_objs["postgres_replication_lag_bytes"].set(info["replication_lag_bytes"])
            except Exception:
                pass

        return True, "ok", info

    except asyncio.TimeoutError:
        return False, "db_timeout", {"probe_latency_ms": int((time.perf_counter() - t0) * 1000)}
    except ImportError:
        return False, "sqlalchemy_not_installed", {}
    except Exception as e:
        return False, f"db_error:{e!s}", {}

# ======================================================================================
# Redis / SMTP / Celery checks
# ======================================================================================
async def _check_redis(timeout: float = 2.0) -> tuple[bool, str]:
    url = settings.REDIS_URL
    if not url:
        return True, "skipped"
    try:
        try:
            import redis.asyncio as aioredis  # type: ignore

            client = _GLOBAL.get("redis")
            if client is None:
                client = aioredis.from_url(url)
                _GLOBAL["redis"] = client

            async def _ping() -> None:
                await client.ping()

            await asyncio.wait_for(_ping(), timeout=timeout)
            return True, "ok"
        except Exception:
            import redis  # type: ignore

            def _ping_sync() -> None:
                r = redis.Redis.from_url(url, socket_timeout=timeout)
                r.ping()

            await asyncio.wait_for(asyncio.to_thread(_ping_sync), timeout=timeout)
            return True, "ok"
    except asyncio.TimeoutError:
        return False, "redis_timeout"
    except ImportError:
        return False, "redis_not_installed"
    except Exception as e:
        return False, f"redis_error:{e!s}"

async def _check_smtp(timeout: float = 3.0) -> tuple[bool, str]:
    host = (settings.SMTP_HOST or "").strip()
    port = int(settings.SMTP_PORT or 0)
    if not host or not port:
        return True, "skipped"
    try:

        def _probe():
            with smtplib.SMTP(host=host, port=port, timeout=timeout) as server:
                server.ehlo()

        await asyncio.wait_for(asyncio.to_thread(_probe), timeout=timeout + 0.5)
        return True, "ok"
    except asyncio.TimeoutError:
        return False, "smtp_timeout"
    except (smtplib.SMTPException, OSError, socket.error) as e:
        return False, f"smtp_error:{e!s}"
    except Exception as e:
        return False, f"smtp_error:{e!s}"

async def _check_celery(timeout: float = 3.0) -> tuple[bool, str, dict]:
    broker = (getattr(settings, "CELERY_BROKER_URL", "") or "").strip()
    backend = (getattr(settings, "CELERY_RESULT_BACKEND", "") or "").strip()
    if not broker:
        return True, "skipped", {}
    t0 = time.perf_counter()
    try:
        try:
            from celery import Celery  # type: ignore
        except Exception:
            return False, "celery_not_installed", {}

        app = _GLOBAL.get("celery")
        if app is None:
            app = Celery(broker=broker, backend=backend or None)
            _GLOBAL["celery"] = app

        def _probe() -> dict:
            out: Dict[str, Any] = {}
            try:
                res = app.control.ping(timeout=timeout)
            except Exception as e:
                out["ping_error"] = str(e)
                res = None
            out["ping_raw"] = res
            workers_responded = 0
            if isinstance(res, list):
                workers_responded = len(res)
            out["workers_responded"] = workers_responded
            try:
                insp = app.control.inspect(timeout=timeout)
                out["active_queues"] = insp.active_queues()  # type: ignore
                out["stats"] = insp.stats()  # type: ignore
            except Exception as e:
                out["inspect_error"] = str(e)
            return out

        info = await asyncio.wait_for(asyncio.to_thread(_probe), timeout=timeout + 0.5)
        duration = time.perf_counter() - t0

        if PROM_AVAILABLE:
            try:
                prom_objs["celery_ping_latency_seconds"].observe(duration)
                if isinstance(info.get("workers_responded"), int):
                    prom_objs["celery_workers_up"].set(info["workers_responded"])
            except Exception:
                pass

        ok = bool(info.get("workers_responded", 0) > 0)
        return (True if ok or broker == "" else False), ("ok" if ok else "no_workers"), info
    except asyncio.TimeoutError:
        return False, "celery_timeout", {}
    except Exception as e:
        return False, f"celery_error:{e!s}", {}

# ======================================================================================
# External API checks
# ======================================================================================
async def _get_httpx_client() -> Any:
    client = _GLOBAL.get("httpx")
    if client is not None:
        return client
    try:
        import httpx  # type: ignore

        client = httpx.AsyncClient(timeout=3.0)
        _GLOBAL["httpx"] = client
        return client
    except Exception:
        _GLOBAL["httpx"] = None
        return None

async def _check_external_api(name: str, url: str, headers: Optional[Dict[str, str]] = None, timeout: float = 3.0) -> tuple[bool, str, dict]:
    if not url:
        return False, "url_missing", {}
    t0 = time.perf_counter()
    client = await _get_httpx_client()
    try:
        if client:
            resp = await client.get(url, headers=headers or {}, timeout=timeout)
            ok = 200 <= resp.status_code < 300
            detail = f"status:{resp.status_code}"
        else:
            import requests  # type: ignore

            def _req():
                r = requests.get(url, headers=headers or {}, timeout=timeout)
                return r.status_code

            code = await asyncio.wait_for(asyncio.to_thread(_req), timeout=timeout + 0.5)
            ok = 200 <= code < 300
            detail = f"status:{code}"
        latency = time.perf_counter() - t0
        if PROM_AVAILABLE:
            try:
                prom_objs["external_api_latency_seconds"].labels(name).observe(latency)
                prom_objs["external_api_up"].labels(name).set(1 if ok else 0)
            except Exception:
                pass
        return ok, detail, {"latency_ms": int(latency * 1000)}
    except asyncio.TimeoutError:
        if PROM_AVAILABLE:
            try:
                prom_objs["external_api_up"].labels(name).set(0)
            except Exception:
                pass
        return False, "timeout", {}
    except Exception as e:
        if PROM_AVAILABLE:
            try:
                prom_objs["external_api_up"].labels(name).set(0)
            except Exception:
                pass
        return False, f"error:{e!s}", {}

async def _check_integrations() -> Dict[str, Dict[str, Any]]:
    tasks = []
    kaspi_url = (getattr(settings, "KASPI_API_URL", "") or "").rstrip("/")
    if kaspi_url:
        tasks.append(_check_external_api("kaspi", f"{kaspi_url}"))
    tiptop_url = (getattr(settings, "TIPTOP_API_URL", "") or "").rstrip("/")
    if tiptop_url:
        tasks.append(_check_external_api("tiptop", f"{tiptop_url}"))
    mobizon_url = (getattr(settings, "MOBIZON_API_URL", "") or "").rstrip("/")
    if mobizon_url:
        tasks.append(_check_external_api("mobizon", f"{mobizon_url}"))

    results: Dict[str, Dict[str, Any]] = {}
    if not tasks:
        return results

    out = await asyncio.gather(*tasks, return_exceptions=True)
    names = [
        "kaspi" if kaspi_url else None,
        "tiptop" if tiptop_url else None,
        "mobizon" if mobizon_url else None,
    ]
    j = 0
    for name in names:
        if not name:
            continue
        res = out[j]
        j += 1
        if isinstance(res, Exception):
            results[name] = {"ok": False, "detail": f"exception:{res!s}"}
        else:
            ok, detail, meta = res
            results[name] = {"ok": ok, "detail": detail, "info": meta}
    return results

# ======================================================================================
# Sliding p99 response time
# ======================================================================================
_LATENCY_BUFFER = deque(maxlen=5000)  # seconds

def _percentile(sorted_vals: List[float], p: float) -> float:
    if not sorted_vals:
        return 0.0
    if p <= 0:
        return sorted_vals[0]
    if p >= 100:
        return sorted_vals[-1]
    k = (len(sorted_vals) - 1) * (p / 100.0)
    f = int(k)
    c = min(f + 1, len(sorted_vals) - 1)
    if f == c:
        return sorted_vals[f]
    d0 = sorted_vals[f] * (c - k)
    d1 = sorted_vals[c] * (k - f)
    return d0 + d1

def _p99_ms() -> int:
    try:
        data = list(_LATENCY_BUFFER)
        if not data:
            return 0
        data.sort()
        return int(_percentile(data, 99) * 1000)
    except Exception:
        return 0

# ======================================================================================
# Async profiling middleware
# ======================================================================================
async def _profiled_call(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
    start = time.perf_counter()
    response: Response
    try:
        response = await call_next(request)
    finally:
        duration = time.perf_counter() - start
        if PROM_AVAILABLE:
            try:
                path = request.url.path
                prom_objs["request_latency_seconds"].labels(request.method, path).observe(duration)
                status_code = getattr(response, "status_code", 200)
                prom_objs["requests_total"].labels(request.method, path, str(status_code)).inc()
            except Exception:
                pass
        try:
            _LATENCY_BUFFER.append(duration)
        except Exception:
            pass
        try:
            ms = int(duration * 1000)
            response.headers["X-Response-Time-ms"] = str(ms)
            response.headers["X-Response-Time-P99-ms"] = str(_p99_ms())
            response.headers.setdefault("Server-Timing", _server_timing_value(ms))
        except Exception:
            pass
        if OTEL_AVAILABLE:
            try:
                from opentelemetry import trace as _trace  # type: ignore

                span = _trace.get_current_span()
                ctx = span.get_span_context() if span else None
                if ctx and ctx.is_valid:
                    trace_id = format(ctx.trace_id, "032x")
                    response.headers.setdefault("X-Trace-Id", trace_id)
            except Exception:
                pass
    return response

# ======================================================================================
# FEATURE FLAGS (in-memory)
# ======================================================================================
_FEATURE_FLAGS: Dict[str, bool] = {}
_FEATURE_LOCK = asyncio.Lock()

def _bootstrap_feature_flags_from_env() -> None:
    import json

    raw = os.getenv("FEATURE_FLAGS_JSON", "")
    if not raw:
        return
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            for k, v in data.items():
                if isinstance(v, bool):
                    _FEATURE_FLAGS[str(k)] = v
    except Exception as e:
        logger.warning("FEATURE_FLAGS_JSON parse error: %s", e)

def get_feature_flag(key: str, default: Optional[bool] = None) -> Optional[bool]:
    return _FEATURE_FLAGS.get(key, default)

# ======================================================================================
# LIFESPAN
# ======================================================================================
@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        logger.info("Application startup… env=%s version=%s", settings.ENVIRONMENT, settings.VERSION)
        try:
            settings.ensure_dirs()
        except Exception as e:
            logger.warning("ensure_dirs failed: %s", e)

        _import_models_once()

        try:
            settings.init_opentelemetry()
        except Exception as e:
            logger.info("settings.init_opentelemetry failed or disabled: %s", e)

        _bootstrap_feature_flags_from_env()

        try:
            from app.api.v1 import campaigns as _  # noqa: F401
            logger.info("Campaigns module detected and ready")
        except Exception as e:
            logger.info("Campaigns module not loaded: %s", e)

        # ---- автозапуск планировщика (по флагу)
        try:
            if getattr(settings, "ENABLE_SCHEDULER", False) and not _GLOBAL.get("scheduler_started"):
                from app.worker import scheduler_worker  # type: ignore
                scheduler_worker.start()
                _GLOBAL["scheduler_started"] = True
                logger.info("APScheduler worker started (ENABLE_SCHEDULER=True)")
        except Exception as e:
            logger.error("Scheduler start failed: %s", e)

    finally:
        yield

    # graceful shutdown
    try:
        client = _GLOBAL.get("httpx")
        if client is not None:
            try:
                await client.aclose()
            except Exception:
                pass
            _GLOBAL["httpx"] = None
    except Exception:
        pass

    try:
        redis_client = _GLOBAL.get("redis")
        if redis_client is not None:
            try:
                await redis_client.close()
            except Exception:
                pass
            _GLOBAL["redis"] = None
    except Exception:
        pass

    try:
        engine = _GLOBAL.get("db_engine")
        if engine is not None:
            try:
                engine.dispose()
            except Exception:
                pass
            _GLOBAL["db_engine"] = None
    except Exception:
        pass

    try:
        _GLOBAL["celery"] = None
    except Exception:
        pass

    # Остановка планировщика
    try:
        if _GLOBAL.get("scheduler_started"):
            from app.worker import scheduler_worker  # type: ignore
            scheduler_worker.stop()
            _GLOBAL["scheduler_started"] = False
            logger.info("APScheduler worker stopped")
    except Exception as e:
        logger.error("Scheduler stop failed: %s", e)

    logger.info("Application shutdown complete.")

# ======================================================================================
# APP FACTORY
# ======================================================================================
def create_app() -> FastAPI:
    enable_docs = os.getenv("ENABLE_DOCS", "1") in ("1", "true", "True")
    docs_url = "/docs" if enable_docs else None
    redoc_url = "/redoc" if enable_docs else None
    openapi_url = "/openapi.json" if enable_docs else None

    app = FastAPI(
        title=settings.APP_NAME,
        version=settings.VERSION,
        debug=settings.DEBUG,
        docs_url=docs_url,
        redoc_url=redoc_url,
        openapi_url=openapi_url,
        lifespan=lifespan,
    )

    cors_origins = getattr(settings, "CORS_ORIGINS", None)
    if isinstance(cors_origins, str):
        cors_origins = [o.strip() for o in cors_origins.split(",") if o.strip()]
    if not cors_origins:
        cors_origins = ["*"]

    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID", "X-Trace-Id", "X-Response-Time-ms", "X-Response-Time-P99-ms", "Server-Timing"],
        max_age=86400,
    )
    app.add_middleware(GZipMiddleware, minimum_size=1024)

    _max_body = int(os.getenv("MAX_REQUEST_SIZE_BYTES", "0") or 0)

    @app.middleware("http")
    async def content_length_guard(request: Request, call_next: Callable[[Request], Awaitable[Response]]):
        try:
            if _max_body > 0:
                cl = request.headers.get("content-length")
                if cl and cl.isdigit() and int(cl) > _max_body:
                    return JSONResponse(status_code=413, content={"detail": "request_entity_too_large"})
        except Exception:
            pass
        return await call_next(request)

    trusted = _parse_trusted_hosts()
    if trusted and TrustedHostMiddleware:
        app.add_middleware(TrustedHostMiddleware, allowed_hosts=trusted)

    session_secret = os.getenv("SESSION_SECRET_KEY", "")
    if session_secret and SessionMiddleware:
        app.add_middleware(
            SessionMiddleware,
            secret_key=session_secret,
            same_site="lax",
            https_only=not settings.DEBUG,
        )

    @app.middleware("http")
    async def security_headers_mw(request: Request, call_next: Callable[[Request], Awaitable[Response]]):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("X-XSS-Protection", "0")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        return response

    if STARLETTE_EXPORTER_AVAILABLE:
        app.add_middleware(PrometheusMiddleware)

    if OTEL_AVAILABLE:
        try:
            FastAPIInstrumentor().instrument_app(app)
            if OTEL_ASGI_AVAILABLE:
                from opentelemetry.instrumentation.asgi import OpenTelemetryMiddleware  # type: ignore
                app.add_middleware(OpenTelemetryMiddleware)
            logger.info("OpenTelemetry FastAPI/ASGI instrumentation is enabled")
        except Exception as e:
            logger.info("OpenTelemetry instrumentation skipped: %s", e)

    @app.middleware("http")
    async def request_id_middleware(request: Request, call_next: Callable[[Request], Awaitable[Response]]):
        req_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        token = _request_id_var.set(req_id)
        try:
            try:
                response = await call_next(request)
            except Exception as e:
                logger.exception("Unhandled error (rid=%s): %s", req_id, e)
                return JSONResponse(status_code=500, content={"detail": "internal_error", "request_id": req_id})
            response.headers["X-Request-ID"] = req_id
            return response
        finally:
            try:
                _request_id_var.reset(token)
            except Exception:
                pass

    @app.middleware("http")
    async def profiling_middleware(request: Request, call_next: Callable[[Request], Awaitable[Response]]):
        return await _profiled_call(request, call_next)

    @app.exception_handler(Exception)
    async def unhandled_exceptions(_: Request, exc: Exception):
        rid = str(uuid.uuid4())
        logger.exception("Unhandled exception (rid=%s): %s", rid, exc)
        return JSONResponse(status_code=500, content={"detail": "internal_error", "request_id": rid})

    # ----------------------------------------------------------------------------------
    # Base endpoints
    # ----------------------------------------------------------------------------------
    def _build_info() -> Dict[str, Any]:
        return {
            "version": settings.VERSION,
            "git_sha": os.getenv("GIT_SHA", ""),
            "build_time": os.getenv("BUILD_TIME", ""),
            "build_number": os.getenv("BUILD_NUMBER", ""),
            "environment": settings.ENVIRONMENT,
            "last_deploy_at": _env_last_deploy_time(),
            "app_name": settings.APP_NAME,
        }

    @app.get("/")
    async def root():
        return {"message": "SmartSell API is running", **_build_info()}

    @app.get("/ping")
    async def ping():
        return PlainTextResponse("pong")

    @app.head("/ping")
    async def ping_head():
        return PlainTextResponse("", status_code=200)

    @app.get("/version")
    async def version():
        return _build_info()

    # Удобные алиасы
    @app.get("/__version")
    async def version_alias():
        return _build_info()

    @app.get("/build")
    async def build():
        return _build_info()

    @app.get("/-/health")
    async def dash_health():
        return RedirectResponse(url="/health", status_code=307)

    @app.get("/-/ready")
    async def dash_ready():
        return RedirectResponse(url="/ready", status_code=307)

    @app.get("/-/live")
    async def dash_live():
        return RedirectResponse(url="/live", status_code=307)

    @app.get("/health")
    async def health():
        do_integrations = os.getenv("HEALTH_CHECK_INTEGRATIONS", "0") in ("1", "true", "True")
        do_celery = os.getenv("HEALTH_CHECK_CELERY", "0") in ("1", "true", "True")

        sync_url = (getattr(settings, "sqlalchemy_urls", {}) or {}).get("sync") or (settings.DATABASE_URL or "")
        sync_url = (sync_url or "").strip()
        pg_ok, pg_msg, pg_info = (
            await _pg_probe(sync_url, timeout=3.0) if _is_postgres_url(sync_url) else (False, "not_postgres_url", {})
        )

        tasks: List[asyncio.Future] = [
            asyncio.to_thread(settings.health_check),
            _check_redis(),
            _check_smtp(),
        ]
        tasks.append(_check_celery() if do_celery else asyncio.sleep(0, result=(True, "skipped", {})))
        tasks.append(_check_integrations() if do_integrations else asyncio.sleep(0, result={}))

        (
            settings_report,
            (redis_ok, redis_msg),
            (smtp_ok, smtp_msg),
            (cel_ok, cel_msg, cel_info),
            integrations,
        ) = await asyncio.gather(*tasks)

        ok = bool(settings_report.get("ok", True))

        if PROM_AVAILABLE:
            try:
                prom_objs["health_status"].set(1 if ok else 0)
            except Exception:
                pass

        return {
            "status": "healthy" if ok else "degraded",
            "version": settings.VERSION,
            "build_info": _build_info(),
            "checks": {
                "settings": settings_report,
                "postgres": {"ok": pg_ok, "detail": pg_msg, "info": pg_info, "checked": _is_postgres_url(sync_url)},
                "redis": {"ok": redis_ok, "detail": redis_msg, "url_set": bool(settings.REDIS_URL)},
                "smtp": {"ok": smtp_ok, "detail": smtp_msg},
                "celery": {"ok": cel_ok, "detail": cel_msg, "info": cel_info, "checked": bool(do_celery)},
                "integrations": {"details": integrations, "checked": bool(do_integrations)},
            },
        }

    @app.get("/ready")
    async def readiness():
        require_redis = os.getenv("READINESS_REQUIRE_REDIS", "0") in ("1", "true", "True")
        require_smtp = os.getenv("READINESS_REQUIRE_SMTP", "0") in ("1", "true", "True")
        require_celery = os.getenv("READINESS_REQUIRE_CELERY", "0") in ("1", "true", "True")
        require_integrations = os.getenv("READINESS_REQUIRE_INTEGRATIONS", "0") in ("1", "true", "True")

        sync_url = (getattr(settings, "sqlalchemy_urls", {}) or {}).get("sync") or (settings.DATABASE_URL or "")
        sync_url = (sync_url or "").strip()
        pg_checked = _is_postgres_url(sync_url)
        if pg_checked:
            pg_ok, pg_msg, _ = await _pg_probe(sync_url, timeout=2.5)
        else:
            pg_ok, pg_msg = (True, "skipped")

        check_redis_f = _check_redis() if require_redis else asyncio.sleep(0, result=(True, "skipped"))
        check_smtp_f = _check_smtp() if require_smtp else asyncio.sleep(0, result=(True, "skipped"))
        check_celery_f = _check_celery() if require_celery else asyncio.sleep(0, result=(True, "skipped", {}))
        check_int_f = _check_integrations() if require_integrations else asyncio.sleep(0, result={})

        (redis_ok, redis_msg), (smtp_ok, smtp_msg), (cel_ok, cel_msg, cel_info), integrations = await asyncio.gather(
            check_redis_f, check_smtp_f, check_celery_f, check_int_f
        )

        integrations_ok = True
        if isinstance(integrations, dict):
            for v in integrations.values():
                if not v.get("ok", False):
                    integrations_ok = False
                    break

        ok = pg_ok and (redis_ok if require_redis else True) and (smtp_ok if require_smtp else True) and (cel_ok if require_celery else True) and (integrations_ok if require_integrations else True)
        payload = {
            "ready": ok,
            "postgres": {"ok": pg_ok, "detail": pg_msg, "checked": bool(pg_checked)},
            "redis": {"ok": redis_ok if require_redis else True, "detail": redis_msg if require_redis else "skipped", "required": bool(require_redis), "url_set": bool(settings.REDIS_URL)},
            "smtp": {"ok": smtp_ok if require_smtp else True, "detail": smtp_msg if require_smtp else "skipped", "required": bool(require_smtp)},
            "celery": {"ok": cel_ok if require_celery else True, "detail": cel_msg if require_celery else "skipped", "required": bool(require_celery), "info": cel_info if require_celery else {}},
            "integrations": {"ok": integrations_ok if require_integrations else True, "details": integrations if require_integrations else {}, "required": bool(require_integrations)},
            "build_info": _build_info(),
        }
        return JSONResponse(status_code=200 if ok else 503, content=payload)

    @app.get("/live")
    async def liveness():
        return PlainTextResponse("OK")

    @app.head("/live")
    async def liveness_head():
        return PlainTextResponse("", status_code=200)

    @app.get("/healthz")
    async def healthz_alias():
        return RedirectResponse(url="/health", status_code=307)

    @app.get("/__health")
    async def underscored_health():
        return RedirectResponse(url="/health", status_code=307)

    @app.get("/dbinfo")
    async def db_info():
        sync_url = (getattr(settings, "sqlalchemy_urls", {}) or {}).get("sync") or (settings.DATABASE_URL or "")
        sync_url = (sync_url or "").strip()
        if not _is_postgres_url(sync_url):
            return JSONResponse(status_code=400, content={"ok": False, "error": "not_postgres_url_or_empty", "url": sync_url})
        ok, msg, info = await _pg_probe(sync_url, timeout=3.0)
        return {"ok": ok, "message": msg, "info": info}

    if STARLETTE_EXPORTER_AVAILABLE:
        app.add_route("/metrics", handle_metrics)
    else:
        try:
            from prometheus_client import CONTENT_TYPE_LATEST, generate_latest  # type: ignore
        except Exception:  # pragma: no cover
            CONTENT_TYPE_LATEST, generate_latest = ("text/plain", lambda *_args, **_kw: b"# no prometheus\n")

        @app.get("/metrics")
        async def metrics():
            if PROM_AVAILABLE:
                try:
                    data = generate_latest(prom_objs["registry"])
                    return PlainTextResponse(data, media_type=CONTENT_TYPE_LATEST)
                except Exception as e:
                    logger.warning("Prometheus generate_latest failed: %s", e)
                    return PlainTextResponse("# metrics error\n", media_type="text/plain")
            return PlainTextResponse("# no prometheus libs installed\n", media_type="text/plain")

    # ----------------------------------------------------------------------------------
    # Feature Flags endpoints
    # ----------------------------------------------------------------------------------
    @app.get("/feature-flags")
    async def list_feature_flags():
        return [{"key": k, "enabled": v} for k, v in sorted(_FEATURE_FLAGS.items())]

    @app.get("/feature-flags/{key}")
    async def get_feature_flag_endpoint(key: str = Path(..., min_length=1)):
        val = _FEATURE_FLAGS.get(key)
        if val is None:
            raise HTTPException(status_code=404, detail="flag_not_found")
        return {"key": key, "enabled": bool(val)}

    @app.put("/feature-flags/{key}")
    async def set_feature_flag_endpoint(
        key: str = Path(..., min_length=1),
        payload: Dict[str, Any] = Body(
            ...,
            examples={
                "enable": {"summary": "Enable a feature flag", "value": {"enabled": True}},
                "disable": {"summary": "Disable a feature flag", "value": {"enabled": False}},
            },
        ),
    ):
        enabled = payload.get("enabled")
        if not isinstance(enabled, bool):
            raise HTTPException(status_code=400, detail="enabled_must_be_boolean")
        async with _FEATURE_LOCK:
            _FEATURE_FLAGS[key] = enabled
        return {"key": key, "enabled": enabled}

    @app.post("/feature-flags/{key}/toggle")
    async def toggle_feature_flag_endpoint(key: str = Path(..., min_length=1)):
        async with _FEATURE_LOCK:
            current = _FEATURE_FLAGS.get(key, False)
            _FEATURE_FLAGS[key] = not current
            val = _FEATURE_FLAGS[key]
        return {"key": key, "enabled": val}

    @app.delete("/feature-flags/{key}")
    async def delete_feature_flag_endpoint(key: str = Path(..., min_length=1)):
        async with _FEATURE_LOCK:
            existed = key in _FEATURE_FLAGS
            if existed:
                _FEATURE_FLAGS.pop(key, None)
        if not existed:
            raise HTTPException(status_code=404, detail="flag_not_found")
        return {"deleted": True, "key": key}

    # ----------------------------------------------------------------------------------
    # Diagnostics & Utilities
    # ----------------------------------------------------------------------------------
    @app.get("/uptime")
    async def uptime():
        return {"uptime_seconds": _uptime_seconds()}

    @app.get("/status")
    async def status_page():
        try:
            return {"ok": True, "uptime_seconds": _uptime_seconds(), "p99_ms": _p99_ms(), "now_ts": int(time.time()), "build": _build_info()}
        except Exception as e:
            return JSONResponse(status_code=500, content={"ok": False, "error": str(e)})

    @app.get("/routes")
    async def list_routes():
        try:
            routes = []
            for r in app.router.routes:
                try:
                    path = getattr(r, "path", None) or getattr(r, "path_format", None) or str(r)
                    methods = sorted(getattr(r, "methods", set()) or [])
                    name = getattr(r, "name", "")
                    routes.append({"path": path, "methods": methods, "name": name})
                except Exception:
                    continue
            routes.sort(key=lambda x: x["path"])
            return {"count": len(routes), "routes": routes}
        except Exception as e:
            return JSONResponse(status_code=500, content={"error": str(e)})

    @app.get("/env")
    async def env_info():
        allow = bool(settings.DEBUG) or (os.getenv("ALLOW_ENV_ENDPOINT", "0") in ("1", "true", "True"))
        if not allow:
            raise HTTPException(status_code=404, detail="not_found")
        try:
            env = _redact_dict(dict(os.environ))
            safe_settings = _redact_dict(
                {
                    "APP_NAME": settings.APP_NAME,
                    "ENVIRONMENT": settings.ENVIRONMENT,
                    "VERSION": settings.VERSION,
                    "DEBUG": bool(settings.DEBUG),
                    "DATABASE_URL": getattr(settings, "DATABASE_URL", None),
                    "REDIS_URL": getattr(settings, "REDIS_URL", None),
                    "SMTP_HOST": getattr(settings, "SMTP_HOST", None),
                    "SMTP_PORT": getattr(settings, "SMTP_PORT", None),
                }
            )
            return {"env": env, "settings": safe_settings}
        except Exception as e:
            return JSONResponse(status_code=500, content={"error": str(e)})

    @app.get("/debug/headers")
    async def debug_headers(request: Request):
        if not settings.DEBUG:
            raise HTTPException(status_code=404, detail="not_found")
        try:
            headers = {k: v for k, v in request.headers.items()}
            return {"method": request.method, "url": str(request.url), "headers": headers}
        except Exception as e:
            return JSONResponse(status_code=500, content={"error": str(e)})

    @app.get("/robots.txt")
    async def robots_txt():
        disallow = os.getenv("ROBOTS_DISALLOW", "0") in ("1", "true", "True")
        body = "User-agent: *\nDisallow: /" if disallow else "User-agent: *\nAllow: /"
        return PlainTextResponse(body)

    # Экспорт OpenAPI в YAML (если установлен pyyaml)
    @app.get("/openapi.yaml")
    async def openapi_yaml():
        if app.openapi is None:
            raise HTTPException(status_code=503, detail="openapi_unavailable")
        try:
            import yaml  # type: ignore
        except Exception:
            return JSONResponse(status_code=501, content={"detail": "pyyaml_not_installed"})
        try:
            spec = app.openapi()
            return PlainTextResponse(yaml.safe_dump(spec, sort_keys=False), media_type="application/yaml")
        except Exception as e:
            return JSONResponse(status_code=500, content={"detail": f"openapi_error:{e!s}"})

    # ----------------------------------------------------------------------------------
    # Routers (агрегатор + защита от дублей)
    # ----------------------------------------------------------------------------------
    _import_models_once()

    # 1) ✅ Подключаем единый реестр v1 (auth, users, products, campaigns, wallet, payments)
    try:
        mount_v1(app, base_prefix="/api/v1")
    except Exception as e:
        logger.exception("mount_v1 failed: %s", e)

    # 2) Fallback campaigns — только если после mount_v1 префикса нет
    if not _has_path_prefix(app, "/api/v1/campaigns"):
        campaigns_mounted = False
        try:
            from app.api.v1.campaigns import router as campaigns_router

            router_prefix = getattr(campaigns_router, "prefix", "") or ""
            if router_prefix.startswith("/api/"):
                app.include_router(campaigns_router, tags=["campaigns"])
            else:
                app.include_router(campaigns_router, prefix="/api/v1/campaigns", tags=["campaigns"])
            campaigns_mounted = True
        except Exception as e:
            logger.warning("Campaigns router not mounted (will use fallback): %s", e)

        if not campaigns_mounted:
            _CAMPAIGNS_FAKE_STORE: List[Dict[str, Any]] = []
            _id_seq = 0

            fallback = APIRouter(prefix="/api/v1/campaigns", tags=["campaigns"])

            def _find_campaign(cid: int) -> Dict[str, Any] | None:
                for it in _CAMPAIGNS_FAKE_STORE:
                    if int(it.get("id")) == cid:
                        return it
                return None

            @fallback.post("/", status_code=201)
            async def create_campaign(payload: Dict[str, Any]):
                nonlocal _id_seq
                _id_seq += 1
                item = {
                    "id": _id_seq,
                    "title": payload.get("title") or f"Campaign #{_id_seq}",
                    "description": payload.get("description"),
                    "messages": payload.get("messages", []),
                    "tags": payload.get("tags", []),
                    "active": bool(payload.get("active", True)),
                    "created_at": int(time.time()),
                    "updated_at": int(time.time()),
                }
                if isinstance(item["messages"], list):
                    for i, m in enumerate(item["messages"]):
                        if "id" not in m:
                            m["id"] = i + 1
                        m.setdefault("status", "pending")
                        m.setdefault("channel", "email")
                _CAMPAIGNS_FAKE_STORE.append(item)
                return item

            @fallback.get("/")
            async def list_campaigns() -> List[Dict[str, Any]]:
                return list(_CAMPAIGNS_FAKE_STORE)

            @fallback.get("/{cid}")
            async def get_campaign(cid: int = Path(..., ge=1)):
                item = _find_campaign(cid)
                if not item:
                    raise HTTPException(status_code=404, detail="not_found")
                return item

            @fallback.put("/{cid}")
            async def update_campaign(cid: int = Path(..., ge=1), payload: Dict[str, Any] = Body(...)):
                item = _find_campaign(cid)
                if not item:
                    raise HTTPException(status_code=404, detail="not_found")
                allowed = {"title", "description", "messages", "tags", "active"}
                changed = False
                for k, v in payload.items():
                    if k in allowed:
                        if k == "messages" and isinstance(v, list):
                            msgs: List[Dict[str, Any]] = []
                            for i, m in enumerate(v, start=1):
                                mm = dict(m)
                                mm.setdefault("id", i)
                                mm.setdefault("status", "pending")
                                mm.setdefault("channel", "email")
                                msgs.append(mm)
                            if item.get("messages") != msgs:
                                item["messages"] = msgs
                                changed = True
                        elif item.get(k) != v:
                            item[k] = v
                            changed = True
                if changed:
                    item["updated_at"] = int(time.time())
                return item

            @fallback.post("/{cid}/tags", status_code=201)
            async def add_tag(cid: int = Path(..., ge=1), payload: Dict[str, Any] = Body(...)):
                item = _find_campaign(cid)
                if not item:
                    raise HTTPException(status_code=404, detail="not_found")
                tag = str(payload.get("tag", "")).strip()
                if not tag:
                    raise HTTPException(status_code=422, detail="tag_required")
                tags = item.setdefault("tags", [])
                if tag not in tags:
                    tags.append(tag)
                    item["updated_at"] = int(time.time())
                return {"id": cid, "tags": tags}

            @fallback.post("/{cid}/messages", status_code=201)
            async def add_message(cid: int = Path(..., ge=1), payload: Dict[str, Any] = Body(...)):
                item = _find_campaign(cid)
                if not item:
                    raise HTTPException(status_code=404, detail="not_found")
                msgs: List[Dict[str, Any]] = item.setdefault("messages", [])
                next_id = (max([m.get("id", 0) for m in msgs]) if msgs else 0) + 1
                msg = {
                    "id": next_id,
                    "recipient": payload.get("recipient"),
                    "content": payload.get("content"),
                    "status": (payload.get("status") or "pending").lower(),
                    "channel": (payload.get("channel") or "email").lower(),
                    "created_at": int(time.time()),
                }
                msgs.append(msg)
                item["updated_at"] = int(time.time())
                return msg

            @fallback.get("/{cid}/messages")
            async def list_messages(cid: int = Path(..., ge=1)):
                item = _find_campaign(cid)
                if not item:
                    raise HTTPException(status_code=404, detail="not_found")
                return item.get("messages", [])

            @fallback.get("/{cid}/stats")
            async def campaign_stats(cid: int = Path(..., ge=1)):
                item = _find_campaign(cid)
                if not item:
                    raise HTTPException(status_code=404, detail="not_found")
                msgs: List[Dict[str, Any]] = item.get("messages", []) or []
                total = len(msgs)
                def _cnt(st: str) -> int:
                    return sum(1 for m in msgs if str(m.get("status", "")).lower() == st)
                pending = _cnt("pending")
                sent = _cnt("sent") + _cnt("delivered")
                failed = _cnt("failed") + _cnt("error")
                return {
                    "id": cid,
                    "title": item.get("title"),
                    "total_messages": total,
                    "pending": pending,
                    "sent": sent,
                    "failed": failed,
                    "tags": item.get("tags", []),
                    "active": item.get("active", True),
                }

            @fallback.delete("/{cid}")
            async def delete_campaign(cid: int = Path(..., ge=1)):
                for i, it in enumerate(_CAMPAIGNS_FAKE_STORE):
                    if int(it.get("id")) == cid:
                        _CAMPAIGNS_FAKE_STORE.pop(i)
                        return {"deleted": True, "id": cid}
                raise HTTPException(status_code=404, detail="not_found")

            app.include_router(fallback)

    # 3) Продукты: подключаем ваш v1-модуль только если агрегатор его не добавил
    if not _has_path_prefix(app, "/api/v1/products"):
        try:
            from app.api.v1.products import router as products_api_router
            app.include_router(products_api_router, prefix="/api/v1", tags=["Products"])
            logger.info("Mounted app.api.v1.products router")
        except Exception as e:
            logger.warning("Products API router not mounted: %s", e)

    # Старые/альтернативные роутеры, если используются в проекте
    try:
        from app.routers.products import router as products_router
        app.include_router(products_router, prefix="/api/v1/products", tags=["products-legacy"])
    except Exception:
        pass

    try:
        from app.routers.users import router as users_router
        app.include_router(users_router, prefix="/api/v1/users", tags=["users"])
    except Exception:
        pass

    # Static/media
    try:  # pragma: no cover
        if StaticFiles:
            if getattr(settings, "STATIC_DIR", None):
                app.mount("/static", StaticFiles(directory=settings.STATIC_DIR), name="static")
            if getattr(settings, "MEDIA_DIR", None):
                app.mount("/media", StaticFiles(directory=settings.MEDIA_DIR), name="media")
    except Exception as e:  # pragma: no cover
        logger.warning("Static/media mount failed: %s", e)

    return app

# Uvicorn/Gunicorn entrypoint
app = create_app()
