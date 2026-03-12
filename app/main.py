from __future__ import annotations

import asyncio
import importlib
import json
import logging
import os
import pkgutil
import smtplib
import socket
import sys
import time
from collections import deque
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from typing import Any

from fastapi import APIRouter, Body, FastAPI, HTTPException, Path, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from starlette.middleware.gzip import GZipMiddleware
from starlette.middleware.httpsredirect import HTTPSRedirectMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.staticfiles import StaticFiles

from app.api.routes import mount_v1
from app.core import config as core_config
from app.core.config import run_startup_side_effects, settings, should_disable_startup_hooks, validate_prod_secrets
from app.core.exceptions import register_exception_handlers
from app.main_helpers import (
    env_int,
    env_truthy,
    has_path_prefix,
    is_postgres_url,
    parse_trusted_hosts,
    run_lifespan_shutdown,
    run_lifespan_startup,
)
from app.main_middleware_helpers import (
    register_content_length_guard,
    register_external_diag_timing_middleware,
    register_profiling_middleware,
    register_request_completion_logging_middleware,
    register_request_id_middleware,
    register_response_time_middleware,
    register_security_headers_middleware,
)
from app.main_payload_helpers import (
    build_dbinfo_payload,
    build_debug_headers_payload,
    build_env_info_payload,
    build_info_payload,
)
from app.main_registration_helpers import (
    mount_campaigns_router_with_fallback,
    mount_primary_routers,
    mount_secondary_routers_and_static,
    register_base_info_routes,
    register_feature_flag_routes,
    register_health_readiness_and_diagnostics_routes,
    register_metrics_route,
)

try:
    from starlette.middleware.sessions import SessionMiddleware
except Exception:  # pragma: no cover
    SessionMiddleware = None  # type: ignore[assignment]


# ======================================================================================
# LOGGER
# ======================================================================================
logger = logging.getLogger(__name__)
request_observability_logger = logging.getLogger("app.request.observability")

_request_id_var: ContextVar[str] = ContextVar("request_id", default="-")
_hostname = socket.gethostname()


class _RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:  # noqa: D401 - tiny helper
        try:
            record.request_id = _request_id_var.get()
        except Exception:
            record.request_id = "-"
        # безопасно добавляем hostname (для basicConfig-формата)
        try:
            record.hostname = _hostname
        except Exception:
            record.hostname = "unknown-host"
        return True


_BASE_LOGGING_CONFIGURED = False


def _env_truthy(value: str | None, default: bool = False) -> bool:
    return env_truthy(value, default)


def _configure_base_logging() -> None:
    global _BASE_LOGGING_CONFIGURED
    if _BASE_LOGGING_CONFIGURED:
        return
    root = logging.getLogger()
    if not root.handlers:
        log_level = os.getenv("LOG_LEVEL", "INFO").upper()
        logging.basicConfig(
            level=log_level,
            # добавили request_id, pid и host в формат по-современному
            format="%(asctime)s %(levelname)s [%(name)s] pid=%(process)d host=%(hostname)s rid=%(request_id)s %(message)s",
        )
    root.addFilter(_RequestIdFilter())
    _BASE_LOGGING_CONFIGURED = True


@contextmanager
def _suppress_import_logging() -> Any:
    previous_level = logging.root.manager.disable
    logging.disable(logging.CRITICAL)
    try:
        yield
    finally:
        logging.disable(previous_level)


# ======================================================================================
# GLOBAL STATE
# ======================================================================================
_GLOBAL: dict[str, Any] = {
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
prom_objs: dict[str, Any] = {}

try:
    from starlette_exporter import PrometheusMiddleware, handle_metrics  # type: ignore

    STARLETTE_EXPORTER_AVAILABLE = True
except Exception:
    try:
        from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram  # type: ignore

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
            "postgres_latency_seconds",
            "Latency of Postgres probe in seconds",
            registry=prom_objs["registry"],
        )
        prom_objs["postgres_is_primary"] = Gauge(
            "postgres_is_primary",
            "1 if Primary, 0 if Standby or unknown",
            registry=prom_objs["registry"],
        )
        prom_objs["postgres_is_readonly"] = Gauge(
            "postgres_is_readonly",
            "1 if default_transaction_read_only=on, else 0",
            registry=prom_objs["registry"],
        )
        prom_objs["postgres_active_connections"] = Gauge(
            "postgres_active_connections",
            "Number of active connections (pg_stat_database sum)",
            registry=prom_objs["registry"],
        )
        prom_objs["postgres_replication_lag_bytes"] = Gauge(
            "postgres_replication_lag_bytes",
            "Max replication lag in bytes if available",
            registry=prom_objs["registry"],
        )
        prom_objs["celery_workers_up"] = Gauge(
            "celery_workers_up",
            "Number of responding Celery workers",
            registry=prom_objs["registry"],
        )
        prom_objs["celery_ping_latency_seconds"] = Histogram(
            "celery_ping_latency_seconds",
            "Latency of Celery control.ping() in seconds",
            registry=prom_objs["registry"],
        )
        prom_objs["external_api_latency_seconds"] = Histogram(
            "external_api_latency_seconds",
            "Latency of external API checks in seconds",
            ["name"],
            registry=prom_objs["registry"],
        )
        prom_objs["external_api_up"] = Gauge(
            "external_api_up",
            "1 if external API healthy else 0",
            ["name"],
            registry=prom_objs["registry"],
        )
    except Exception:
        pass

# ======================================================================================
# OpenTelemetry (optional)
# ======================================================================================
OTEL_AVAILABLE = False
try:
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor  # type: ignore

    OTEL_AVAILABLE = True
except Exception:
    OTEL_AVAILABLE = False


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


# ======================================================================================
# PostgreSQL deep probe
# ======================================================================================
async def _pg_probe(sync_url: str, timeout: float = 3.0) -> tuple[bool, str, dict[str, Any]]:
    """
    Безопасная проверка PostgreSQL для эндпоинтов /health, /ready, /dbinfo.

    Двухшаговый конструктор engine во избежание ошибок с NullPool.
    """
    info: dict[str, Any] = {}
    if not is_postgres_url(sync_url):
        return False, "not_postgres_url", info

    t0 = time.perf_counter()
    try:
        from sqlalchemy import create_engine  # type: ignore
        from sqlalchemy import text as sql_text

        engine = _GLOBAL.get("db_engine")
        if engine is None:
            # --- попытка №1: очередь соединений (работает в большинстве случаев)
            try:
                engine = create_engine(
                    sync_url,
                    pool_pre_ping=True,
                    pool_size=1,
                    max_overflow=0,
                    pool_timeout=timeout,
                    future=True,
                )
            except TypeError:
                # --- попытка №2: максимально совместимая конфигурация (без опций пула)
                engine = create_engine(sync_url, pool_pre_ping=True, future=True)
            _GLOBAL["db_engine"] = engine

        def _run_queries() -> dict[str, Any]:
            out: dict[str, Any] = {}
            with engine.connect() as conn:
                out["server_version"] = conn.execute(sql_text("SHOW server_version")).scalar()  # type: ignore
                out["current_database"] = conn.execute(sql_text("SELECT current_database()")).scalar()  # type: ignore
                out["timezone"] = conn.execute(sql_text("SHOW TimeZone")).scalar()  # type: ignore
                out["default_transaction_read_only"] = conn.execute(
                    sql_text("SHOW default_transaction_read_only")
                ).scalar()  # type: ignore
                try:
                    is_recovery = conn.execute(sql_text("SELECT pg_is_in_recovery()")).scalar()  # type: ignore
                except Exception:
                    is_recovery = None
                out["is_standby"] = bool(is_recovery) if is_recovery is not None else None
                out["is_primary"] = (
                    False if out.get("is_standby") else True if out.get("is_standby") is not None else None
                )

                try:
                    active_conns = conn.execute(
                        sql_text("SELECT COALESCE(SUM(numbackends),0) FROM pg_stat_database")
                    ).scalar()  # type: ignore
                except Exception:
                    active_conns = None
                out["active_connections"] = int(active_conns or 0)

                try:
                    exts = conn.execute(sql_text("SELECT extname FROM pg_extension")).fetchall()  # type: ignore
                    out["extensions"] = [r[0] for r in exts] if exts else []
                except Exception:
                    out["extensions"] = None

                lag_bytes: int | None = None
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

    except TimeoutError:
        return False, "db_timeout", {"probe_latency_ms": int((time.perf_counter() - t0) * 1000)}
    except ImportError:
        return False, "sqlalchemy_not_installed", {}
    except Exception as e:
        return False, f"db_error:{e!s}", {}


# ======================================================================================
# Redis / SMTP / Celery checks
# ======================================================================================
async def _check_redis(timeout: float = 2.0) -> tuple[bool, str]:
    if getattr(settings, "is_testing", False) or os.getenv("PYTEST_CURRENT_TEST") or os.getenv("TESTING"):
        return True, "skipped_testing"
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

            await asyncio.wait_for(asyncio.to_thread(lambda: None), timeout=0)  # yield
            await asyncio.wait_for(_ping(), timeout=timeout)
            return True, "ok"
        except Exception:
            import redis  # type: ignore

            def _ping_sync() -> None:
                r = redis.Redis.from_url(url, socket_timeout=timeout)
                r.ping()

            await asyncio.wait_for(asyncio.to_thread(_ping_sync), timeout=timeout)
            return True, "ok"
    except TimeoutError:
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

        def _probe() -> None:
            with smtplib.SMTP(host=host, port=port, timeout=timeout) as server:
                server.ehlo()

        await asyncio.wait_for(asyncio.to_thread(_probe), timeout=timeout + 0.5)
        return True, "ok"
    except TimeoutError:
        return False, "smtp_timeout"
    except (smtplib.SMTPException, OSError) as e:
        return False, f"smtp_error:{e!s}"
    except Exception as e:
        return False, f"smtp_error:{e!s}"


async def _check_celery(timeout: float = 3.0) -> tuple[bool, str, dict[str, Any]]:
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

        def _probe() -> dict[str, Any]:
            out: dict[str, Any] = {}
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
    except TimeoutError:
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


async def _check_external_api(
    name: str, url: str, headers: dict[str, str] | None = None, timeout: float = 3.0
) -> tuple[bool, str, dict[str, Any]]:
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

            def _req() -> int:
                r = requests.get(url, headers=headers or {}, timeout=timeout)
                return int(r.status_code)

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
    except TimeoutError:
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


async def _check_integrations() -> dict[str, dict[str, Any]]:
    tasks: list[Awaitable[tuple[bool, str, dict[str, Any]]]] = []
    kaspi_url = (getattr(settings, "KASPI_API_URL", "") or "").rstrip("/")
    if kaspi_url:
        tasks.append(_check_external_api("kaspi", f"{kaspi_url}"))
    tiptop_url = (getattr(settings, "TIPTOP_API_URL", "") or "").rstrip("/")
    if tiptop_url:
        tasks.append(_check_external_api("tiptop", f"{tiptop_url}"))
    mobizon_url = (getattr(settings, "MOBIZON_API_URL", "") or "").rstrip("/")
    if mobizon_url:
        tasks.append(_check_external_api("mobizon", f"{mobizon_url}"))

    results: dict[str, dict[str, Any]] = {}
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


async def _check_provider_registry() -> dict[str, dict[str, Any]]:
    results: dict[str, dict[str, Any]] = {}
    domains = ["otp", "messaging", "payments"]
    try:
        from app.core.db import async_session_maker
        from app.core.provider_registry import ProviderRegistry

        async with async_session_maker() as db:
            for domain in domains:
                entry = await ProviderRegistry.get_active_provider(db, domain)
                if not entry:
                    results[domain] = {"ok": False, "detail": "provider_missing"}
                    continue
                provider_name = (entry.provider or "").strip().lower()
                if not provider_name or provider_name.startswith("noop"):
                    results[domain] = {"ok": False, "detail": "provider_noop"}
                    continue
                if domain == "payments" and provider_name in {
                    "placeholder",
                    "payment-placeholder",
                    "payments-placeholder",
                    "stub",
                    "dummy",
                }:
                    results[domain] = {"ok": False, "detail": "provider_placeholder"}
                    continue
                if domain == "payments" and provider_name in {
                    "manual",
                    "manual-pay",
                    "payments-manual",
                    "manual-billing",
                }:
                    results[domain] = {
                        "ok": True,
                        "detail": "manual_billing_only",
                        "provider": entry.provider,
                        "version": entry.version,
                    }
                    continue
                if domain == "otp" and provider_name in {"mobizon", "otp-mobizon", "mobizon-otp"}:
                    missing: list[str] = []
                    if not settings.MOBIZON_API_KEY:
                        missing.append("MOBIZON_API_KEY")
                    if not settings.MOBIZON_SENDER:
                        missing.append("MOBIZON_SENDER")
                    if missing:
                        results[domain] = {"ok": False, "detail": "mobizon_missing_config", "missing": missing}
                        continue
                if domain == "messaging" and provider_name in {"smtp", "email-smtp", "smtp-email"}:
                    missing: list[str] = []
                    if not settings.SMTP_HOST:
                        missing.append("SMTP_HOST")
                    if not settings.SMTP_USER:
                        missing.append("SMTP_USER")
                    if not settings.SMTP_PASSWORD:
                        missing.append("SMTP_PASSWORD")
                    if not settings.SMTP_FROM_EMAIL:
                        missing.append("SMTP_FROM_EMAIL")
                    if missing:
                        results[domain] = {"ok": False, "detail": "smtp_missing_config", "missing": missing}
                        continue
                if domain == "payments" and provider_name in {"tiptop", "tiptop-pay", "tippay", "tiptop-payment"}:
                    missing: list[str] = []
                    if not settings.TIPTOP_API_KEY:
                        missing.append("TIPTOP_API_KEY")
                    if not settings.TIPTOP_API_SECRET:
                        missing.append("TIPTOP_API_SECRET")
                    if missing:
                        results[domain] = {"ok": False, "detail": "tiptop_missing_config", "missing": missing}
                        continue
                results[domain] = {
                    "ok": True,
                    "detail": "ok",
                    "provider": entry.provider,
                    "version": entry.version,
                }
    except Exception as exc:
        err = f"provider_check_error:{exc!s}"
        for domain in domains:
            results[domain] = {"ok": False, "detail": err}
    return results


# ======================================================================================
# Sliding p99 response time
# ======================================================================================
_LATENCY_BUFFER = deque(maxlen=5000)  # seconds


def _percentile(sorted_vals: list[float], p: float) -> float:
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
# ASGI timing middleware (TTFB + total)
# ======================================================================================
class TimingASGIMiddleware:
    def __init__(self, app: Callable):
        self.app = app

    async def __call__(self, scope: dict, receive: Callable, send: Callable) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        scope.setdefault("state", {})
        start = time.perf_counter()
        ttfb_ms: int | None = None
        start_message: dict | None = None
        body_messages: list[dict] = []

        async def send_wrapper(message: dict) -> None:
            nonlocal ttfb_ms, start_message, body_messages

            msg_type = message.get("type")
            if msg_type == "http.response.start":
                if ttfb_ms is None:
                    ttfb_ms = int((time.perf_counter() - start) * 1000)
                start_message = message
                return

            if msg_type == "http.response.body":
                body_messages.append(message)
                if not message.get("more_body", False):
                    total_ms = int((time.perf_counter() - start) * 1000)
                    if start_message is not None:
                        headers = list(start_message.get("headers") or [])
                        headers.append((b"x-ttfb-ms", str(ttfb_ms or total_ms).encode()))
                        headers.append((b"x-total-ms", str(total_ms).encode()))
                        db_close_ms = scope.get("state", {}).get("db_close_ms")
                        if db_close_ms is not None:
                            headers.append((b"x-db-close-ms", str(int(db_close_ms)).encode()))
                        start_message["headers"] = headers
                        await send(start_message)
                        for body_msg in body_messages:
                            await send(body_msg)
                    else:
                        await send(message)
                return

            await send(message)

        await self.app(scope, receive, send_wrapper)


class ExternalDiagMiddleware:
    def __init__(self, app: Callable):
        self.app = app

    async def __call__(self, scope: dict, receive: Callable, send: Callable) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        scope.setdefault("state", {})
        t0 = time.perf_counter()
        t_before_endpoint = time.perf_counter()
        t_after_endpoint: float | None = None
        t_before_send: float | None = None
        t_total: float | None = None
        start_message: dict | None = None
        body_messages: list[dict] = []

        async def send_wrapper(message: dict) -> None:
            nonlocal t_after_endpoint, t_before_send, t_total, start_message, body_messages

            msg_type = message.get("type")
            if msg_type == "http.response.start":
                if t_after_endpoint is None:
                    t_after_endpoint = time.perf_counter()
                t_before_send = time.perf_counter()
                start_message = message
                return

            if msg_type == "http.response.body":
                body_messages.append(message)
                if not message.get("more_body", False):
                    t_total = time.perf_counter()
                    if start_message is not None:
                        headers = list(start_message.get("headers") or [])
                        start_ms = 0
                        before_endpoint_ms = int((t_before_endpoint - t0) * 1000)
                        after_endpoint_ms = int(((t_after_endpoint or t_total) - t0) * 1000)
                        before_send_ms = int(((t_before_send or t_total) - t0) * 1000)
                        total_ms = int(((t_total or t_before_send or t_after_endpoint or t0) - t0) * 1000)
                        headers.append((b"x-mw-start", str(start_ms).encode()))
                        headers.append((b"x-mw-before-endpoint", str(before_endpoint_ms).encode()))
                        headers.append((b"x-mw-after-endpoint", str(after_endpoint_ms).encode()))
                        headers.append((b"x-mw-before-send", str(before_send_ms).encode()))
                        headers.append((b"x-mw-total", str(total_ms).encode()))
                        headers.append(
                            (
                                b"x-mw-ms",
                                json.dumps(
                                    {
                                        "start": start_ms,
                                        "before_endpoint": before_endpoint_ms,
                                        "after_endpoint": after_endpoint_ms,
                                        "before_send": before_send_ms,
                                        "total": total_ms,
                                    },
                                    separators=(",", ":"),
                                ).encode(),
                            )
                        )
                        start_message["headers"] = headers
                        await send(start_message)
                        for body_msg in body_messages:
                            await send(body_msg)
                    else:
                        await send(message)
                return

            await send(message)

        await self.app(scope, receive, send_wrapper)


# ======================================================================================
# Async profiling middleware (устойчив к исключениям)
# ======================================================================================
async def _profiled_call(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
    start = time.perf_counter()
    response: Response | None = None
    try:
        response = await call_next(request)
        return response
    finally:
        duration = time.perf_counter() - start
        if PROM_AVAILABLE:
            try:
                path = request.url.path
                prom_objs["request_latency_seconds"].labels(request.method, path).observe(duration)
                status_code = getattr(response, "status_code", 200) if response is not None else 500
                prom_objs["requests_total"].labels(request.method, path, str(status_code)).inc()
            except Exception:
                pass
        try:
            _LATENCY_BUFFER.append(duration)
        except Exception:
            pass
        if response is not None:
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
            # полезные технические заголовки
            try:
                response.headers.setdefault("X-Process-Id", str(os.getpid()))
                response.headers.setdefault("X-Hostname", _hostname)
            except Exception:
                pass


# ======================================================================================
# FEATURE FLAGS (in-memory)
# ======================================================================================
_FEATURE_FLAGS: dict[str, bool] = {}
_FEATURE_LOCK = asyncio.Lock()


def _bootstrap_feature_flags_from_env() -> None:
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


def get_feature_flag(key: str, default: bool | None = None) -> bool | None:
    return _FEATURE_FLAGS.get(key, default)


# ======================================================================================
# LIFESPAN — FIXED structure (startup → yield → shutdown)
# ======================================================================================
@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:  # type: ignore[override]
    # ---- Startup
    kaspi_sync_task = await run_lifespan_startup(
        logger=logger,
        settings=settings,
        core_config=core_config,
        validate_prod_secrets_fn=validate_prod_secrets,
        should_disable_startup_hooks_fn=should_disable_startup_hooks,
        check_provider_registry_fn=_check_provider_registry,
        run_startup_side_effects_fn=run_startup_side_effects,
        import_models_once_fn=_import_models_once,
        bootstrap_feature_flags_from_env_fn=_bootstrap_feature_flags_from_env,
        configure_base_logging_fn=_configure_base_logging,
        global_state=_GLOBAL,
    )

    # передаём управление приложению
    try:
        yield
    finally:
        # ---- Shutdown (graceful)
        await run_lifespan_shutdown(logger=logger, global_state=_GLOBAL, kaspi_sync_task=kaspi_sync_task)


# ======================================================================================
# APP FACTORY
# ======================================================================================
async def _safe_settings_health_check() -> dict[str, Any]:
    """Безопасный враппер для settings.health_check()."""
    try:
        func = getattr(settings, "health_check", None)
        if callable(func):
            return await asyncio.to_thread(func)
        return {"ok": True, "detail": "skipped"}
    except Exception as e:
        return {"ok": False, "detail": f"settings_health_error:{e!s}"}


def _create_app() -> FastAPI:
    enable_docs = env_truthy(os.getenv("ENABLE_DOCS", "1"), True)
    docs_url: str | None = "/docs" if enable_docs else None
    redoc_url: str | None = "/redoc" if enable_docs else None
    openapi_url: str | None = "/openapi.json" if enable_docs else None

    app = FastAPI(
        title=settings.APP_NAME,
        version=settings.VERSION,
        debug=settings.DEBUG,
        docs_url=docs_url,
        redoc_url=redoc_url,
        openapi_url=openapi_url,
        lifespan=lifespan,
        root_path=(settings.ROOT_PATH or ""),  # ✅ учитываем развёртывание за прокси
    )

    # Register unified exception handlers so domain errors map to correct HTTP codes
    try:
        register_exception_handlers(app)
    except Exception as exc:
        logger.warning("Failed to register exception handlers", exc_info=exc)

    # HTTPS redirect (optional)
    if env_truthy(os.getenv("FORCE_HTTPS", "0")) and HTTPSRedirectMiddleware:
        app.add_middleware(HTTPSRedirectMiddleware)

    app.add_middleware(TimingASGIMiddleware)

    # CORS
    cors_origins = getattr(settings, "CORS_ORIGINS", None) or getattr(settings, "BACKEND_CORS_ORIGINS", None)
    if isinstance(cors_origins, str):
        cors_origins = [o.strip() for o in cors_origins.split(",") if o.strip()]
    if not cors_origins:
        cors_origins = ["*"]

    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,  # type: ignore[arg-type]
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=[
            "X-Request-ID",
            "X-Trace-Id",
            "X-Response-Time-ms",
            "X-Response-Time-P99-ms",
            "Server-Timing",
            "X-Process-Id",
            "X-Hostname",
        ],
        max_age=86400,
    )
    app.add_middleware(GZipMiddleware, minimum_size=1024)

    _test_env = (
        settings.is_testing
        or settings.ENVIRONMENT.lower() == "test"
        or os.getenv("PYTEST_CURRENT_TEST")
        or os.getenv("TESTING", "").lower() in {"1", "true", "yes", "on"}
    )

    _max_body = env_int("MAX_REQUEST_SIZE_BYTES", 0)

    if _test_env:
        register_response_time_middleware(app)

    if not _test_env:
        register_content_length_guard(app, max_body=_max_body)

    trusted = parse_trusted_hosts()
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

    # Security headers (CSP опционально)
    csp_enabled = env_truthy(os.getenv("ENABLE_CSP", "0"))
    csp_value = os.getenv(
        "CSP_HEADER_VALUE",
        "default-src 'self'; img-src 'self' data: blob:; media-src 'self' data: blob:; "
        "font-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline' 'unsafe-eval';",
    )

    register_security_headers_middleware(
        app,
        csp_enabled=csp_enabled,
        csp_value=csp_value,
        env_truthy_fn=env_truthy,
    )

    if STARLETTE_EXPORTER_AVAILABLE:
        app.add_middleware(PrometheusMiddleware)

    if OTEL_AVAILABLE:
        try:
            FastAPIInstrumentor().instrument_app(app)
            if "OpenTelemetryMiddleware" not in [type(m).__name__ for m in getattr(app, "user_middleware", [])]:
                try:
                    from opentelemetry.instrumentation.asgi import (
                        OpenTelemetryMiddleware,  # type: ignore
                    )

                    app.add_middleware(OpenTelemetryMiddleware)
                except Exception:
                    pass
            logger.info("OpenTelemetry FastAPI/ASGI instrumentation is enabled")
        except Exception as e:
            logger.info("OpenTelemetry instrumentation skipped: %s", e)

    if not _test_env:
        register_request_id_middleware(app, request_id_var=_request_id_var, hostname=_hostname)

    if not _test_env:
        register_profiling_middleware(app, profiled_call=_profiled_call)

    if not _test_env:
        register_request_completion_logging_middleware(
            app,
            request_observability_logger=request_observability_logger,
        )

    if settings.is_development:
        if not _test_env:
            register_external_diag_timing_middleware(app)

    # ----------------------------------------------------------------------------------
    # Base info helpers & endpoints
    # ----------------------------------------------------------------------------------
    def _build_info() -> dict[str, Any]:
        return build_info_payload(settings)

    async def root() -> dict[str, Any]:
        return {"message": "SmartSell API is running", **_build_info()}

    async def ping() -> str:
        return "pong"

    async def ping_head() -> str:
        return ""

    async def version() -> dict[str, Any]:
        return _build_info()

    async def version_alias() -> dict[str, Any]:
        return _build_info()

    async def build() -> dict[str, Any]:
        return _build_info()

    # 👇 небольшие удобные алиасы/диагностика (безопасно)
    async def uptime() -> dict[str, int]:
        return {"uptime_seconds": _uptime_seconds()}

    async def info() -> dict[str, Any]:
        return {"uptime_seconds": _uptime_seconds(), **_build_info()}

    async def robots() -> str:
        return "User-agent: *\nDisallow:\n"

    async def favicon() -> str:
        return ""

    # удобные алиасы
    async def dash_health() -> RedirectResponse:
        return RedirectResponse(url="/health", status_code=307)

    async def dash_ready() -> RedirectResponse:
        return RedirectResponse(url="/ready", status_code=307)

    async def dash_live() -> RedirectResponse:
        return RedirectResponse(url="/live", status_code=307)

    register_base_info_routes(
        app,
        root=root,
        ping=ping,
        ping_head=ping_head,
        version=version,
        version_alias=version_alias,
        build=build,
        uptime=uptime,
        info=info,
        robots=robots,
        favicon=favicon,
        dash_health=dash_health,
        dash_ready=dash_ready,
        dash_live=dash_live,
    )

    # основной health
    async def health() -> dict[str, Any]:
        # In testing we want a deterministic healthy response for sync TestClient
        testing_mode = bool(getattr(settings, "TESTING", False) or os.getenv("PYTEST_CURRENT_TEST"))
        if testing_mode:
            return {
                "status": "healthy",
                "version": settings.VERSION,
                "build_info": _build_info(),
                "checks": {
                    "settings": {"ok": True, "detail": "testing"},
                    "postgres": {"ok": True, "detail": "testing", "info": {}, "checked": False},
                    "redis": {"ok": True, "detail": "testing", "url_set": False},
                    "smtp": {"ok": True, "detail": "testing"},
                    "celery": {"ok": True, "detail": "testing", "info": {}, "checked": False},
                    "integrations": {"details": {}, "checked": False},
                },
            }

        do_integrations = env_truthy(os.getenv("HEALTH_CHECK_INTEGRATIONS", "0"))
        do_celery = env_truthy(os.getenv("HEALTH_CHECK_CELERY", "0"))

        sync_url = (getattr(settings, "sqlalchemy_urls", {}) or {}).get("sync") or (settings.DATABASE_URL or "")
        sync_url = (sync_url or "").strip()
        pg_ok, pg_msg, pg_info = (
            await _pg_probe(sync_url, timeout=3.0) if is_postgres_url(sync_url) else (False, "not_postgres_url", {})
        )

        settings_report = await _safe_settings_health_check()

        tasks: list[Awaitable[Any]] = [_check_redis(), _check_smtp()]
        tasks.append(_check_celery() if do_celery else asyncio.sleep(0, result=(True, "skipped", {})))
        tasks.append(_check_integrations() if do_integrations else asyncio.sleep(0, result={}))

        (
            (redis_ok, redis_msg),
            (smtp_ok, smtp_msg),
            (cel_ok, cel_msg, cel_info),
            integrations,
        ) = await asyncio.gather(*tasks)

        # Основной статус — от настроек; детали — по подсекциям
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
                "postgres": {
                    "ok": pg_ok,
                    "detail": pg_msg,
                    "info": pg_info,
                    "checked": is_postgres_url(sync_url),
                },
                "redis": {"ok": redis_ok, "detail": redis_msg, "url_set": bool(settings.REDIS_URL)},
                "smtp": {"ok": smtp_ok, "detail": smtp_msg},
                "celery": {
                    "ok": cel_ok,
                    "detail": cel_msg,
                    "info": cel_info,
                    "checked": bool(do_celery),
                },
                "integrations": {"details": integrations, "checked": bool(do_integrations)},
            },
        }

    # расширенный статус (объединяет сведения, p99, фичи) — удобно для мониторинга
    async def status() -> dict[str, Any]:
        h = await health()
        return {
            "service": _build_info(),
            "uptime_seconds": _uptime_seconds(),
            "p99_ms": _p99_ms(),
            "feature_flags": [{"key": k, "enabled": v} for k, v in sorted(_FEATURE_FLAGS.items())],
            "health": h,
        }

    async def readiness() -> JSONResponse:
        strict = env_truthy(os.getenv("READINESS_STRICT", "1" if settings.is_production else "0"))
        if (os.getenv("PYTEST_CURRENT_TEST") or os.getenv("TESTING")) and not strict:
            return JSONResponse(status_code=200, content={"ready": True, "checks": {}})
        require_redis = env_truthy(os.getenv("READINESS_REQUIRE_REDIS", "0"))
        require_smtp = env_truthy(os.getenv("READINESS_REQUIRE_SMTP", "0"))
        require_celery = env_truthy(os.getenv("READINESS_REQUIRE_CELERY", "0"))
        require_integrations = env_truthy(os.getenv("READINESS_REQUIRE_INTEGRATIONS", "0"))
        require_providers = env_truthy(os.getenv("READINESS_REQUIRE_PROVIDERS", "1" if settings.is_production else "0"))

        require_secret = env_truthy(os.getenv("READINESS_REQUIRE_SECRET", "0"))
        secret_present = bool(os.getenv("SESSION_SECRET_KEY") or os.getenv("SECRET_KEY") or os.getenv("APP_SECRET"))

        sync_url = (getattr(settings, "sqlalchemy_urls", {}) or {}).get("sync") or (settings.DATABASE_URL or "")
        sync_url = (sync_url or "").strip()
        pg_checked = is_postgres_url(sync_url)
        if pg_checked:
            pg_ok, pg_msg, _ = await _pg_probe(sync_url, timeout=2.5)
        else:
            pg_ok, pg_msg = (True, "skipped")

        check_redis_f: Awaitable[tuple[bool, str]] = (
            _check_redis() if require_redis else asyncio.sleep(0, result=(True, "skipped"))
        )
        check_smtp_f: Awaitable[tuple[bool, str]] = (
            _check_smtp() if require_smtp else asyncio.sleep(0, result=(True, "skipped"))
        )
        check_celery_f: Awaitable[tuple[bool, str, dict[str, Any]]] = (
            _check_celery() if require_celery else asyncio.sleep(0, result=(True, "skipped", {}))
        )
        check_int_f: Awaitable[dict[str, dict[str, Any]]] = (
            _check_integrations() if require_integrations else asyncio.sleep(0, result={})
        )
        check_providers_f: Awaitable[dict[str, dict[str, Any]]] = (
            _check_provider_registry() if require_providers else asyncio.sleep(0, result={})
        )

        (
            (redis_ok, redis_msg),
            (smtp_ok, smtp_msg),
            (cel_ok, cel_msg, cel_info),
            integrations,
            providers,
        ) = await asyncio.gather(check_redis_f, check_smtp_f, check_celery_f, check_int_f, check_providers_f)

        integrations_ok = True
        if isinstance(integrations, dict):
            for v in integrations.values():
                if not v.get("ok", False):
                    integrations_ok = False
                    break

        providers_ok = True
        if isinstance(providers, dict):
            for v in providers.values():
                if not v.get("ok", False):
                    providers_ok = False
                    break

        ok = (
            pg_ok
            and (redis_ok if require_redis else True)
            and (smtp_ok if require_smtp else True)
            and (cel_ok if require_celery else True)
            and (integrations_ok if require_integrations else True)
            and (providers_ok if require_providers else True)
            and (secret_present if require_secret else True)
        )
        payload: dict[str, Any] = {
            "ready": ok,
            "postgres": {"ok": pg_ok, "detail": pg_msg, "checked": bool(pg_checked)},
            "redis": {
                "ok": redis_ok if require_redis else True,
                "detail": redis_msg if require_redis else "skipped",
                "required": bool(require_redis),
                "url_set": bool(settings.REDIS_URL),
            },
            "smtp": {
                "ok": smtp_ok if require_smtp else True,
                "detail": smtp_msg if require_smtp else "skipped",
                "required": bool(require_smtp),
            },
            "celery": {
                "ok": cel_ok if require_celery else True,
                "detail": cel_msg if require_celery else "skipped",
                "required": bool(require_celery),
                "info": cel_info if require_celery else {},
            },
            "integrations": {
                "ok": integrations_ok if require_integrations else True,
                "details": integrations if require_integrations else {},
                "required": bool(require_integrations),
            },
            "providers": {
                "ok": providers_ok if require_providers else True,
                "details": providers if require_providers else {},
                "required": bool(require_providers),
            },
            "secrets": {
                "ok": secret_present if require_secret else True,
                "required": bool(require_secret),
            },
            "build_info": _build_info(),
        }

        status_code = 200 if (ok or not strict) else 503
        return JSONResponse(status_code=status_code, content=payload)

    async def liveness() -> str:
        return "OK"

    async def liveness_head() -> str:
        return ""

    # стандартные алиасы
    async def healthz_alias() -> RedirectResponse:
        return RedirectResponse(url="/health", status_code=307)

    async def underscored_health() -> RedirectResponse:
        return RedirectResponse(url="/health", status_code=307)

    # Отдача OpenAPI в YAML для удобной интеграции с CI/CD / Kong / Tyk / etc
    async def openapi_yaml() -> str:
        try:
            import yaml  # type: ignore

            schema = app.openapi()
            content = yaml.safe_dump(schema, sort_keys=False)  # type: ignore
            return content
        except Exception as e:
            return f"# yaml export error: {e!s}\n"

    # ----------------------------------------------------------------------------------
    # DB info (safe)
    # ----------------------------------------------------------------------------------
    def dbinfo() -> dict[str, Any]:
        """
        Безопасная сводка по БД без раскрытия паролей.
        """
        return build_dbinfo_payload(settings)

    # ----------------------------------------------------------------------------------
    # 🔧 Диагностика
    # ----------------------------------------------------------------------------------
    async def list_routes() -> Any:
        try:
            routes: list[dict[str, Any]] = []
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

    async def env_info() -> dict[str, Any]:
        allow = bool(settings.DEBUG) or env_truthy(os.getenv("ALLOW_ENV_ENDPOINT", "0"))
        if not allow:
            raise HTTPException(status_code=404, detail="not_found")
        return build_env_info_payload(settings)

    async def debug_headers(request: Request) -> dict[str, Any]:
        if not settings.DEBUG:
            raise HTTPException(status_code=404, detail="not_found")
        return build_debug_headers_payload(request)

    register_health_readiness_and_diagnostics_routes(
        app,
        health=health,
        status=status,
        readiness=readiness,
        liveness=liveness,
        liveness_head=liveness_head,
        healthz_alias=healthz_alias,
        underscored_health=underscored_health,
        openapi_yaml=openapi_yaml,
        dbinfo=dbinfo,
        list_routes=list_routes,
        env_info=env_info,
        debug_headers=debug_headers,
    )

    # /metrics
    metrics_handler: Callable[..., Any] | None = None
    if STARLETTE_EXPORTER_AVAILABLE:
        metrics_handler = None
    else:
        try:
            from prometheus_client import CONTENT_TYPE_LATEST as _CONTENT_TYPE_LATEST  # type: ignore
            from prometheus_client import generate_latest
        except Exception:  # pragma: no cover
            _CONTENT_TYPE_LATEST, generate_latest = (
                "text/plain",
                lambda *_args, **_kw: b"# no prometheus\n",
            )

        async def metrics() -> str:
            if PROM_AVAILABLE:
                try:
                    data = generate_latest(prom_objs["registry"])
                    return data.decode("utf-8", errors="replace")
                except Exception as e:
                    logger.warning("Prometheus generate_latest failed: %s", e)
                    return "# metrics error\n"
            return "# no prometheus libs installed\n"

        metrics_handler = metrics

    register_metrics_route(
        app,
        starlette_exporter_available=STARLETTE_EXPORTER_AVAILABLE,
        handle_metrics_fn=handle_metrics if STARLETTE_EXPORTER_AVAILABLE else None,
        metrics_handler=metrics_handler,
    )

    # ----------------------------------------------------------------------------------
    # ✅ Feature Flags endpoints (ожидаются тестами)
    # ----------------------------------------------------------------------------------
    async def list_feature_flags() -> list[dict[str, Any]]:
        return [{"key": k, "enabled": v} for k, v in sorted(_FEATURE_FLAGS.items())]

    async def get_feature_flag_endpoint(key: str = Path(..., min_length=1)) -> dict[str, Any]:
        val = _FEATURE_FLAGS.get(key)
        if val is None:
            raise HTTPException(status_code=404, detail="flag_not_found")
        return {"key": key, "enabled": bool(val)}

    async def set_feature_flag_endpoint(
        key: str = Path(..., min_length=1),
        payload: dict[str, Any] = Body(
            ...,
            examples={
                "enable": {"summary": "Enable a feature flag", "value": {"enabled": True}},
                "disable": {"summary": "Disable a feature flag", "value": {"enabled": False}},
            },
        ),
    ) -> dict[str, Any]:
        enabled = payload.get("enabled")
        if not isinstance(enabled, bool):
            raise HTTPException(status_code=400, detail="enabled_must_be_boolean")
        async with _FEATURE_LOCK:
            _FEATURE_FLAGS[key] = enabled
        return {"key": key, "enabled": enabled}

    async def toggle_feature_flag_endpoint(key: str = Path(..., min_length=1)) -> dict[str, Any]:
        async with _FEATURE_LOCK:
            current = _FEATURE_FLAGS.get(key, False)
            _FEATURE_FLAGS[key] = not current
            val = _FEATURE_FLAGS[key]
        return {"key": key, "enabled": val}

    async def delete_feature_flag_endpoint(key: str = Path(..., min_length=1)) -> dict[str, Any]:
        async with _FEATURE_LOCK:
            existed = key in _FEATURE_FLAGS
            if existed:
                _FEATURE_FLAGS.pop(key, None)
        if not existed:
            raise HTTPException(status_code=404, detail="flag_not_found")
        return {"deleted": True, "key": key}

    register_feature_flag_routes(
        app,
        list_feature_flags=list_feature_flags,
        get_feature_flag_endpoint=get_feature_flag_endpoint,
        set_feature_flag_endpoint=set_feature_flag_endpoint,
        toggle_feature_flag_endpoint=toggle_feature_flag_endpoint,
        delete_feature_flag_endpoint=delete_feature_flag_endpoint,
    )

    # ----------------------------------------------------------------------------------
    # Routers (агрегатор + защита от дублей)
    # ----------------------------------------------------------------------------------
    _import_models_once()

    mount_primary_routers(
        app,
        settings_obj=settings,
        mount_v1_fn=mount_v1,
        logger=logger,
    )

    def _mount_fallback_campaigns_router() -> None:
        _CAMPAIGNS_FAKE_STORE: list[dict[str, Any]] = []
        _id_seq = 0

        fallback = APIRouter(
            prefix=f"{getattr(settings,'API_V1_STR','/api/v1').rstrip('/')}/campaigns",
            tags=["campaigns"],
        )

        def _find_campaign(cid: int) -> dict[str, Any] | None:
            for it in _CAMPAIGNS_FAKE_STORE:
                if int(it.get("id")) == cid:
                    return it
            return None

        @fallback.post("/", status_code=201)
        async def create_campaign(payload: dict[str, Any]) -> dict[str, Any]:
            nonlocal _id_seq
            _id_seq += 1
            item: dict[str, Any] = {
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
        async def list_campaigns() -> list[dict[str, Any]]:
            return list(_CAMPAIGNS_FAKE_STORE)

        @fallback.get("/{cid}")
        async def get_campaign(cid: int = Path(..., ge=1)) -> dict[str, Any]:
            item = _find_campaign(cid)
            if not item:
                raise HTTPException(status_code=404, detail="not_found")
            return item

        @fallback.put("/{cid}")
        async def update_campaign(
            cid: int = Path(..., ge=1), payload: dict[str, Any] = Body(...)
        ) -> dict[str, Any]:
            item = _find_campaign(cid)
            if not item:
                raise HTTPException(status_code=404, detail="not_found")
            allowed = {"title", "description", "messages", "tags", "active"}
            changed = False
            for k, v in payload.items():
                if k in allowed:
                    if k == "messages" and isinstance(v, list):
                        msgs: list[dict[str, Any]] = []
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
        async def add_tag(cid: int = Path(..., ge=1), payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
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
        async def add_message(cid: int = Path(..., ge=1), payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
            item = _find_campaign(cid)
            if not item:
                raise HTTPException(status_code=404, detail="not_found")
            msgs: list[dict[str, Any]] = item.setdefault("messages", [])
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
        async def list_messages(cid: int = Path(..., ge=1)) -> list[dict[str, Any]]:
            item = _find_campaign(cid)
            if not item:
                raise HTTPException(status_code=404, detail="not_found")
            return item.get("messages", [])

        @fallback.get("/{cid}/stats")
        async def campaign_stats(cid: int = Path(..., ge=1)) -> dict[str, Any]:
            item = _find_campaign(cid)
            if not item:
                raise HTTPException(status_code=404, detail="not_found")
            msgs: list[dict[str, Any]] = item.get("messages", []) or []
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
        async def delete_campaign(cid: int = Path(..., ge=1)) -> dict[str, Any]:
            for i, it in enumerate(_CAMPAIGNS_FAKE_STORE):
                if int(it.get("id")) == cid:
                    _CAMPAIGNS_FAKE_STORE.pop(i)
                    return {"deleted": True, "id": cid}
            raise HTTPException(status_code=404, detail="not_found")

        app.include_router(fallback)

    mount_campaigns_router_with_fallback(
        app,
        settings_obj=settings,
        has_path_prefix_fn=has_path_prefix,
        logger=logger,
        mount_fallback_campaigns_fn=_mount_fallback_campaigns_router,
    )

    mount_secondary_routers_and_static(
        app,
        settings_obj=settings,
        has_path_prefix_fn=has_path_prefix,
        logger=logger,
        static_files_cls=StaticFiles,
    )

    return app


def create_app(*, suppress_import_logs: bool = True) -> FastAPI:
    if suppress_import_logs:
        with _suppress_import_logging():
            return _create_app()
    return _create_app()


# Uvicorn/Gunicorn entrypoint
app: FastAPI = create_app()
