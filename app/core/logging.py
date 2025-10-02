# app/core/logging.py
"""
Centralized logging for SmartSell3 (enterprise-grade, future-proof).

Features:
- Stdlib logging (dictConfig) + structlog (JSON in prod, dev console otherwise).
- Weekly TimedRotating for app log + size-based rotating for error log.
- Sensitive fields redaction.
- Context (request_id, user_id, tenant, client_ip, user_agent) via contextvars.
- OTEL trace_id/span_id enrichment (when OpenTelemetry is active).
- Prometheus counters & histogram for logs/slow queries (best-effort).
- Optional StatsD (best-effort).
- Critical alerts via Webhook/Telegram with throttling & retries (best-effort).
- Slow SQL query tracer for SQLAlchemy sync/async engines.
- Cloud logging integration: Google Cloud Logging / AWS CloudWatch (if enabled & deps installed).
- FastAPI middleware for request context & access logs.
- Uvicorn integration (no duplicate handlers).
- Startup summary, dynamic level change, bridge to DB query stats.

Env knobs (optional):
  LOG_PATH=logs/app.log
  LOG_LEVEL=INFO
  LOG_FORMAT=json|text
  ENVIRONMENT=production|development|test
  ENABLE_PROMETHEUS_LOG_METRICS=1
  ENABLE_STATSD=1, STATSD_HOST=127.0.0.1, STATSD_PORT=8125, STATSD_PREFIX=smartsell3
  ALERT_WEBHOOK_URL=https://example/hooks/...
  TELEGRAM_BOT_TOKEN=..., TELEGRAM_CHAT_ID=...
  ENABLE_GCLOUD_LOGGING=1
  ENABLE_CLOUDWATCH_LOGGING=1, CLOUDWATCH_LOG_GROUP=smartsell3, CLOUDWATCH_REGION=...
  SLOW_SQL_THRESHOLD_MS=300
"""

from __future__ import annotations

import json
import logging
import logging.config
import os
import sys
import time
import uuid
import threading
from contextlib import contextmanager
from contextvars import ContextVar, Token
from datetime import datetime
from typing import Any, Dict, Optional, Tuple

# ---------- Safe settings import ----------
try:
    from app.core.config import settings  # type: ignore
except Exception:  # pragma: no cover
    class _Stub:
        ENVIRONMENT = os.getenv("ENVIRONMENT", "development")
        DEBUG = os.getenv("DEBUG", "0") in ("1", "true", "True")
        LOG_PATH = os.getenv("LOG_PATH", "logs/app.log")
        LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
        LOG_FORMAT = os.getenv("LOG_FORMAT", "json")
        PROJECT_NAME = "SmartSell3"
        VERSION = "0.1.0"
        SENTRY_DSN = os.getenv("SENTRY_DSN")
        PG_SEARCH_PATH = os.getenv("PG_SEARCH_PATH", "")

        def dump_settings_safe(self) -> dict:  # noqa: D401
            return {}

        def build_info(self) -> dict:  # noqa: D401
            return {}

    settings = _Stub()  # type: ignore

# ---------- Optional libs best-effort ----------
try:
    import structlog
    _HAS_STRUCTLOG = True
except Exception:  # pragma: no cover
    structlog = None  # type: ignore
    _HAS_STRUCTLOG = False

try:
    from prometheus_client import Counter, Histogram
    _HAS_PROM = True
except Exception:
    _HAS_PROM = False

try:
    from statsd import StatsClient
    _HAS_STATSD = True
except Exception:
    _HAS_STATSD = False

# ---------- Context vars ----------
_ctx_request_id: ContextVar[str] = ContextVar("request_id", default="")
_ctx_user_id: ContextVar[str] = ContextVar("user_id", default="")
_ctx_tenant: ContextVar[str] = ContextVar("tenant", default="")
_ctx_client_ip: ContextVar[str] = ContextVar("client_ip", default="")
_ctx_user_agent: ContextVar[str] = ContextVar("user_agent", default="")
_ctx_trace_id: ContextVar[str] = ContextVar("trace_id", default="")
_ctx_span_id: ContextVar[str] = ContextVar("span_id", default="")

_CONFIGURED = False

# ---------- Secrets redaction ----------
_SECRET_KEYS = ("secret", "password", "token", "dsn", "api_key", "api_secret", "access_key", "key")


def _mask_secret_value(v: Any) -> Any:
    try:
        s = str(v)
    except Exception:
        return "***"
    if len(s) <= 6:
        return "***"
    return s[:3] + "***" + s[-3:]


def redact_secrets(data: Any) -> Any:
    if isinstance(data, dict):
        out: Dict[str, Any] = {}
        for k, v in data.items():
            lk = str(k).lower()
            if any(x in lk for x in _SECRET_KEYS) and "public" not in lk:
                out[k] = _mask_secret_value(v)
            else:
                out[k] = redact_secrets(v)
        return out
    if isinstance(data, list):
        return [redact_secrets(v) for v in data]
    if isinstance(data, tuple):
        return tuple(redact_secrets(v) for v in data)
    return data


# ---------- OTEL trace/span injection ----------
def _otel_trace_injector(_, __, event_dict):
    try:
        from opentelemetry import trace  # type: ignore
        span = trace.get_current_span()
        ctx = span.get_span_context() if span else None
        if ctx and getattr(ctx, "is_valid", lambda: False)():
            trace_id = "{:032x}".format(ctx.trace_id)
            span_id = "{:016x}".format(ctx.span_id)
            event_dict["trace_id"] = trace_id
            event_dict["span_id"] = span_id
    except Exception:
        # fallback к contextvars
        tid, sid = _ctx_trace_id.get(), _ctx_span_id.get()
        if tid:
            event_dict["trace_id"] = tid
        if sid:
            event_dict["span_id"] = sid
    return event_dict


# ---------- structlog processors ----------
def _inject_context(_, __, event_dict):
    rid = _ctx_request_id.get()
    uid = _ctx_user_id.get()
    ten = _ctx_tenant.get()
    cip = _ctx_client_ip.get()
    ua = _ctx_user_agent.get()
    tid = _ctx_trace_id.get()
    sid = _ctx_span_id.get()
    if rid:
        event_dict["request_id"] = rid
    if uid:
        event_dict["user_id"] = uid
    if ten:
        event_dict["tenant"] = ten
    if cip:
        event_dict["client_ip"] = cip
    if ua:
        event_dict["user_agent"] = ua
    if tid:
        event_dict["trace_id"] = tid
    if sid:
        event_dict["span_id"] = sid
    return event_dict


def _redact_processor(_, __, event_dict):
    return redact_secrets(event_dict)


def _add_app(build_info: bool = True):
    def _inner(_, __, event_dict):
        event_dict["app"] = getattr(settings, "PROJECT_NAME", "SmartSell3")
        event_dict["version"] = getattr(settings, "VERSION", "")
        if build_info:
            try:
                bi = getattr(settings, "build_info", None)
                if callable(bi):
                    event_dict["build"] = bi()  # type: ignore
            except Exception:
                pass
        return event_dict

    return _inner


# ---------- Prometheus / StatsD ----------
_ENABLE_PROM = os.getenv("ENABLE_PROMETHEUS_LOG_METRICS", "0") in ("1", "true", "True")
_ENABLE_STATSD = os.getenv("ENABLE_STATSD", "0") in ("1", "true", "True")

if _HAS_PROM and _ENABLE_PROM:
    LOG_EVENTS_TOTAL = Counter("smartsell3_log_events_total", "Log events total", ["level"])
    SLOW_SQL_SECONDS = Histogram(
        "smartsell3_slow_sql_seconds",
        "Slow SQL query duration seconds",
        buckets=(0.05, 0.1, 0.2, 0.5, 1, 2, 5, 10),
    )
else:
    LOG_EVENTS_TOTAL = None
    SLOW_SQL_SECONDS = None

if _HAS_STATSD and _ENABLE_STATSD:
    _statsd = StatsClient(
        host=os.getenv("STATSD_HOST", "127.0.0.1"),
        port=int(os.getenv("STATSD_PORT", "8125")),
        prefix=os.getenv("STATSD_PREFIX", "smartsell3"),
    )
else:
    _statsd = None


def _metrics_on_log(level: str) -> None:
    try:
        if LOG_EVENTS_TOTAL is not None:
            LOG_EVENTS_TOTAL.labels(level=level).inc()
        if _statsd is not None:
            _statsd.incr(f"logs.{level.lower()}")
    except Exception:
        pass


def _metrics_on_slow_sql(seconds: float) -> None:
    try:
        if SLOW_SQL_SECONDS is not None:
            SLOW_SQL_SECONDS.observe(seconds)
        if _statsd is not None:
            _statsd.timing("sql.slow_ms", int(seconds * 1000))
    except Exception:
        pass


# ---------- Alerts: Webhook / Telegram ----------
_ALERT_WEBHOOK_URL = os.getenv("ALERT_WEBHOOK_URL", "").strip()
_TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
_TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()


class CriticalAlertHandler(logging.Handler):
    """
    Отправляет уведомления при level >= CRITICAL в Webhook/Telegram.
    Best-effort: не ломает приложение при сбоях сети, имеет простую защиту от флуд-штормов.
    """

    def __init__(self, throttle_seconds: int = 60, retries: int = 2):
        super().__init__(level=logging.CRITICAL)
        self.throttle_seconds = throttle_seconds
        self.retries = retries
        self._last_sent_ts = 0.0

    def emit(self, record: logging.LogRecord) -> None:  # pragma: no cover (сетевой best-effort)
        now = time.time()
        if now - self._last_sent_ts < self.throttle_seconds:
            return
        self._last_sent_ts = now

        try:
            msg = self.format(record)
        except Exception:
            msg = f"CRITICAL: {record.getMessage()}"

        payload = {
            "app": getattr(settings, "PROJECT_NAME", "SmartSell3"),
            "env": getattr(settings, "ENVIRONMENT", "development"),
            "time": datetime.utcnow().isoformat(),
            "message": msg,
            "logger": record.name,
        }

        def _send():
            for i in range(self.retries + 1):
                try:
                    if _ALERT_WEBHOOK_URL:
                        try:
                            import requests
                            requests.post(_ALERT_WEBHOOK_URL, json=payload, timeout=3)
                        except Exception:
                            pass
                    if _TELEGRAM_BOT_TOKEN and _TELEGRAM_CHAT_ID:
                        try:
                            import requests
                            text = f"🚨 *CRITICAL* `{payload['app']}` `{payload['env']}`\n```\n{msg}\n```"
                            requests.post(
                                f"https://api.telegram.org/bot{_TELEGRAM_BOT_TOKEN}/sendMessage",
                                data={"chat_id": _TELEGRAM_CHAT_ID, "text": text, "parse_mode": "Markdown"},
                                timeout=3,
                            )
                        except Exception:
                            pass
                    break
                except Exception:
                    time.sleep(0.5 * (2 ** i))

        threading.Thread(target=_send, daemon=True).start()


# ---------- Stdlib dictConfig ----------
def _build_stdlib_dict_config(logs_dir: str) -> dict:
    os.makedirs(logs_dir, exist_ok=True)

    fmt_console = (
        "%(asctime)s [%(levelname)s] %(name)s:%(filename)s:%(funcName)s:%(lineno)d - %(message)s"
        if getattr(settings, "ENVIRONMENT", "development") != "production"
        else "%(asctime)s [%(levelname)s] %(name)s:%(lineno)d - %(message)s"
    )
    fmt_file = "%(asctime)s [%(levelname)s] %(name)s:%(filename)s:%(funcName)s:%(lineno)d - %(message)s"

    # Weekly rotate app log on Monday (W0), keep 5 backups.
    app_log_handler = {
        "level": "DEBUG",
        "class": "logging.handlers.TimedRotatingFileHandler",
        "when": "W0",
        "backupCount": 5,
        "formatter": "file_detailed",
        "filename": os.path.join(logs_dir, "smartsell3.log"),
        "encoding": "utf8",
    }

    error_log_handler = {
        "level": "ERROR",
        "class": "logging.handlers.RotatingFileHandler",
        "maxBytes": 10 * 1024 * 1024,
        "backupCount": 5,
        "formatter": "file_detailed",
        "filename": os.path.join(logs_dir, "errors.log"),
        "encoding": "utf8",
    }

    # Critical alerts handler (format uses file_detailed for richer context)
    critical_alert_handler = {
        "level": "CRITICAL",
        "class": f"{__name__}.CriticalAlertHandler",
        "formatter": "file_detailed",
    }

    level = (getattr(settings, "LOG_LEVEL", "INFO") or "INFO").upper()

    config = {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "console": {"format": fmt_console, "datefmt": "%Y-%m-%d %H:%M:%S"},
            "file_detailed": {"format": fmt_file, "datefmt": "%Y-%m-%d %H:%M:%S"},
        },
        "handlers": {
            "console": {"level": level, "class": "logging.StreamHandler", "formatter": "console", "stream": "ext://sys.stdout"},
            "file": app_log_handler,
            "error_file": error_log_handler,
            "critical_alerts": critical_alert_handler,
        },
        "loggers": {
            "": {
                "handlers": ["console", "file", "error_file", "critical_alerts"],
                "level": level,
                "propagate": False,
            },
            "uvicorn": {"handlers": ["console", "file"], "level": "INFO", "propagate": False},
            "uvicorn.error": {"handlers": ["console", "file", "error_file"], "level": "INFO", "propagate": False},
            "uvicorn.access": {"handlers": ["console", "file"], "level": "INFO", "propagate": False},
        },
    }
    return config


# ---------- Sentry best-effort ----------
def _try_init_sentry() -> None:
    dsn = getattr(settings, "SENTRY_DSN", None)
    if not dsn:
        return
    try:
        import sentry_sdk
        from sentry_sdk.integrations.logging import LoggingIntegration

        sentry_logging = LoggingIntegration(level=logging.INFO, event_level=logging.ERROR)
        sentry_sdk.init(
            dsn=dsn,
            integrations=[sentry_logging],
            environment=getattr(settings, "ENVIRONMENT", "development"),
            release=f"{getattr(settings, 'PROJECT_NAME', 'SmartSell3')}@{getattr(settings, 'VERSION', '')}",
            traces_sample_rate=0.0,
        )
        logging.getLogger(__name__).info("Sentry initialized")
    except Exception as e:  # pragma: no cover
        logging.getLogger(__name__).warning("Sentry init failed: %s", e)


# ---------- Cloud logging best-effort ----------
def _try_init_gcloud_logging() -> None:
    if os.getenv("ENABLE_GCLOUD_LOGGING", "0") not in ("1", "true", "True"):
        return
    try:
        from google.cloud import logging as gcloud_logging  # type: ignore

        client = gcloud_logging.Client()  # потребует creds
        client.setup_logging()  # перенастраивает root handlers под Google
        logging.getLogger(__name__).info("Google Cloud Logging integrated")
    except Exception as e:
        logging.getLogger(__name__).warning("GCloud logging init failed: %s", e)


def _try_init_cloudwatch_logging() -> None:
    if os.getenv("ENABLE_CLOUDWATCH_LOGGING", "0") not in ("1", "true", "True"):
        return
    try:
        import watchtower  # type: ignore

        group = os.getenv("CLOUDWATCH_LOG_GROUP", "smartsell3")
        region = os.getenv("CLOUDWATCH_REGION", None)
        handler = watchtower.CloudWatchLogHandler(log_group=group, create_log_group=True, region_name=region)
        root = logging.getLogger()
        root.addHandler(handler)
        logging.getLogger(__name__).info("AWS CloudWatch logging integrated (group=%s)", group)
    except Exception as e:
        logging.getLogger(__name__).warning("CloudWatch logging init failed: %s", e)


# ---------- structlog configure ----------
def _configure_structlog() -> None:
    if not _HAS_STRUCTLOG:
        return
    processors = [
        structlog.stdlib.filter_by_level,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso"),
        _inject_context,
        _otel_trace_injector,
        _redact_processor,
        _add_app(build_info=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.UnicodeDecoder(),
    ]
    renderer = (
        structlog.processors.JSONRenderer()
        if getattr(settings, "ENVIRONMENT", "development") == "production"
        else structlog.dev.ConsoleRenderer()
    )
    processors.append(renderer)

    structlog.configure(
        processors=processors,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=logging.DEBUG if getattr(settings, "DEBUG", False) else logging.INFO,
    )


# ---------- Public API ----------
def setup_logging() -> None:
    """
    Centralized logging setup:
    - stdlib dictConfig (console + weekly file + error file + critical alerts)
    - structlog (JSON/console)
    - Sentry + Cloud loggers (best-effort)
    """
    global _CONFIGURED
    if _CONFIGURED:
        return

    logs_dir = os.path.dirname(getattr(settings, "LOG_PATH", "logs/app.log")) or "logs"
    logging.config.dictConfig(_build_stdlib_dict_config(logs_dir))
    _configure_structlog()
    _try_init_sentry()
    _try_init_gcloud_logging()
    _try_init_cloudwatch_logging()

    # Startup notes
    lg = logging.getLogger(__name__)
    lg.info("Logging initialized")
    lg.info("Log files location: %s", os.path.abspath(logs_dir))
    try:
        dump = getattr(settings, "dump_settings_safe", None)
        if callable(dump):
            lg.debug("settings=%s", json.dumps(dump(), ensure_ascii=False))
    except Exception:
        pass

    _CONFIGURED = True


# Backward-compatible alias expected by app.core.__init__ and tests
def configure_logging() -> None:
    """Alias for setup_logging() to keep backward compatibility."""
    setup_logging()


def get_logger(name: str):
    if _HAS_STRUCTLOG:
        return structlog.get_logger(name)
    return logging.getLogger(name)


# ---------- Context helpers ----------
def _set_ctx(
    request_id: Optional[str] = None,
    user_id: Optional[str] = None,
    tenant: Optional[str] = None,
    client_ip: Optional[str] = None,
    user_agent: Optional[str] = None,
    trace_id: Optional[str] = None,
    span_id: Optional[str] = None,
) -> None:
    if request_id is not None:
        _ctx_request_id.set(request_id)
    if user_id is not None:
        _ctx_user_id.set(str(user_id))
    if tenant is not None:
        _ctx_tenant.set(str(tenant))
    if client_ip is not None:
        _ctx_client_ip.set(client_ip)
    if user_agent is not None:
        _ctx_user_agent.set(user_agent)
    if trace_id is not None:
        _ctx_trace_id.set(trace_id)
    if span_id is not None:
        _ctx_span_id.set(span_id)


def clear_context() -> None:
    """Clear all logging context variables."""
    _ctx_request_id.set("")
    _ctx_user_id.set("")
    _ctx_tenant.set("")
    _ctx_client_ip.set("")
    _ctx_user_agent.set("")
    _ctx_trace_id.set("")
    _ctx_span_id.set("")


# Functional API (compat with earlier imports): set & keep until cleared
def bind_context(
    request_id: Optional[str] = None,
    user_id: Optional[str] = None,
    tenant: Optional[str] = None,
    client_ip: Optional[str] = None,
    user_agent: Optional[str] = None,
    trace_id: Optional[str] = None,
    span_id: Optional[str] = None,
) -> None:
    """
    Bind context values for subsequent logs (until clear_context() or override).
    Kept for backward compatibility with earlier code importing `bind_context`.
    """
    _set_ctx(
        request_id=request_id,
        user_id=user_id,
        tenant=tenant,
        client_ip=client_ip,
        user_agent=user_agent,
        trace_id=trace_id,
        span_id=span_id,
    )


# Context manager variant (scoped binding with automatic reset)
@contextmanager
def bound_context(
    request_id: Optional[str] = None,
    user_id: Optional[str] = None,
    tenant: Optional[str] = None,
    client_ip: Optional[str] = None,
    user_agent: Optional[str] = None,
    trace_id: Optional[str] = None,
    span_id: Optional[str] = None,
):
    tokens: list[Tuple[ContextVar[str], Token]] = []
    if request_id is not None:
        tokens.append((_ctx_request_id, _ctx_request_id.set(request_id)))
    if user_id is not None:
        tokens.append((_ctx_user_id, _ctx_user_id.set(str(user_id))))
    if tenant is not None:
        tokens.append((_ctx_tenant, _ctx_tenant.set(str(tenant))))
    if client_ip is not None:
        tokens.append((_ctx_client_ip, _ctx_client_ip.set(client_ip)))
    if user_agent is not None:
        tokens.append((_ctx_user_agent, _ctx_user_agent.set(user_agent)))
    if trace_id is not None:
        tokens.append((_ctx_trace_id, _ctx_trace_id.set(trace_id)))
    if span_id is not None:
        tokens.append((_ctx_span_id, _ctx_span_id.set(span_id)))
    try:
        yield
    finally:
        for var, tok in reversed(tokens):
            var.reset(tok)


# ---------- Request enrichment ----------
def _parse_traceparent(header: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Parse W3C traceparent header: '00-<trace_id>-<span_id>-<flags>'
    """
    try:
        parts = header.strip().split("-")
        if len(parts) >= 3:
            return parts[1], parts[2]
    except Exception:
        pass
    return None, None


def enrich_from_request(request: Any) -> None:
    """
    Extract request_id / client IP / user-agent / OTEL traceparent from Starlette/FastAPI Request
    and bind to logging context (functional API).
    """
    try:
        headers = getattr(request, "headers", {}) or {}
        # Starlette Headers is case-insensitive mapping
        def _h(name: str) -> Optional[str]:
            try:
                return headers.get(name) or headers.get(name.lower())
            except Exception:
                return None

        request_id = _h("X-Request-ID") or _h("X-Correlation-ID") or str(uuid.uuid4())
        user_agent = _h("User-Agent") or ""
        traceparent = _h("traceparent") or ""
        trace_id = span_id = None
        if traceparent:
            trace_id, span_id = _parse_traceparent(traceparent)

        client = getattr(request, "client", None)
        client_ip = ""
        if client and isinstance(client, (list, tuple)):
            client_ip = client[0] or ""
        else:
            client_ip = getattr(client, "host", "") or ""

        bind_context(
            request_id=request_id,
            client_ip=client_ip,
            user_agent=user_agent,
            trace_id=trace_id,
            span_id=span_id,
        )
    except Exception:
        # silent fallback
        return


def set_level_for(logger_name: str, level: str | int) -> None:
    logging.getLogger(logger_name).setLevel(level)


def log_startup_summary() -> None:
    lg = get_logger("startup")
    info = {
        "environment": getattr(settings, "ENVIRONMENT", "development"),
        "debug": bool(getattr(settings, "DEBUG", False)),
        "version": getattr(settings, "VERSION", ""),
        "project": getattr(settings, "PROJECT_NAME", ""),
    }
    if _HAS_STRUCTLOG:
        lg.info("startup_summary", **info)
    else:
        logging.getLogger("startup").info("startup_summary %s", info)


# ---------- Audit Logger ----------
class AuditLogger:
    def __init__(self):
        self.logger = get_logger("audit")

    def log_auth_success(self, user_id: int | str, ip_address: str, user_agent: str) -> None:
        self.logger.info("auth_success", user_id=user_id, ip_address=ip_address, user_agent=user_agent)

    def log_auth_failure(self, username: str, ip_address: str, reason: str) -> None:
        self.logger.warning("auth_failure", username=username, ip_address=ip_address, reason=reason)

    def log_data_change(
        self,
        user_id: int | str,
        action: str,
        resource_type: str,
        resource_id: str | int,
        changes: dict[str, Any],
    ) -> None:
        self.logger.info(
            "data_change",
            user_id=user_id,
            action=action,
            resource_type=resource_type,
            resource_id=str(resource_id),
            changes=redact_secrets(changes),
        )

    def log_security_event(self, event: str, details: dict[str, Any]) -> None:
        self.logger.warning("security_event", event=event, **redact_secrets(details))

    def log_permission_denied(self, user_id: int | str, reason: str, resource: str) -> None:
        self.logger.warning("permission_denied", user_id=user_id, reason=reason, resource=resource)

    # универсальный action, используемый в ваших роутерах
    def log_action(
        self,
        *,
        action: str,
        user_id: int | str | None = None,
        company_id: int | str | None = None,
        entity_type: str | None = None,
        entity_id: int | str | None = None,
        old_values: dict[str, Any] | None = None,
        new_values: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.logger.info(
            "audit_action",
            action=action,
            user_id=user_id,
            company_id=company_id,
            entity_type=entity_type,
            entity_id=entity_id,
            old_values=redact_secrets(old_values or {}),
            new_values=redact_secrets(new_values or {}),
            metadata=redact_secrets(metadata or {}),
        )


audit_logger = AuditLogger()


# ---------- FastAPI middleware ----------
class LoggingContextMiddleware:
    """
    - Generates/reads X-Request-ID
    - Binds request context
    - Logs start/end with duration and status
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
        request_id = headers.get("x-request-id") or headers.get("x-correlation-id") or str(uuid.uuid4())
        client = scope.get("client") or ("", 0)
        client_ip = client[0] if isinstance(client, (list, tuple)) and client else ""
        user_agent = headers.get("user-agent", "")
        path = scope.get("path", "")
        method = scope.get("method", "")

        start = time.perf_counter()
        status_code_holder = {"code": 500}

        async def _send(message):
            if message["type"] == "http.response.start":
                status_code_holder["code"] = message.get("status", 200)
            await send(message)

        with bound_context(request_id=request_id, client_ip=client_ip, user_agent=user_agent):
            lg = get_logger("http")
            lg.info("request_start", method=method, path=path)
            try:
                await self.app(scope, receive, _send)
            finally:
                dur_ms = (time.perf_counter() - start) * 1000.0
                lg.info(
                    "request_end",
                    method=method,
                    path=path,
                    status=status_code_holder["code"],
                    duration_ms=round(dur_ms, 2),
                )


# ---------- Uvicorn integration ----------
def integrate_uvicorn_loggers() -> None:
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        lg = logging.getLogger(name)
        lg.propagate = False


# ---------- Log metrics hook via handlers ----------
class _MetricsHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        try:
            level = record.levelname.upper()
            _metrics_on_log(level)
        except Exception:
            pass


def _attach_metrics_handler() -> None:
    try:
        root = logging.getLogger()
        root.addHandler(_MetricsHandler())
    except Exception:
        pass


# ---------- Slow SQL tracer for SQLAlchemy ----------
def enable_sqlalchemy_slow_query_logging(engine_or_sync_engine, threshold_ms: Optional[int] = None) -> None:
    """
    Подключает обработчики событий SQLAlchemy для логирования/метрик «медленных» запросов.
    Аргументы:
      engine_or_sync_engine: Engine (sync) или async_engine.sync_engine
      threshold_ms: порог в миллисекундах (по умолчанию берётся из env SLOW_SQL_THRESHOLD_MS или 300)
    """
    try:
        from sqlalchemy import event  # type: ignore
    except Exception:
        logging.getLogger(__name__).warning("SQLAlchemy not available; slow query logging is disabled.")
        return

    sync_engine = getattr(engine_or_sync_engine, "sync_engine", engine_or_sync_engine)
    thr = threshold_ms or int(os.getenv("SLOW_SQL_THRESHOLD_MS", "300"))

    @event.listens_for(sync_engine, "before_cursor_execute")
    def before_cursor_execute(conn, cursor, statement, params, context, executemany):  # noqa: D401
        context._query_start_time = time.perf_counter()

    @event.listens_for(sync_engine, "after_cursor_execute")
    def after_cursor_execute(conn, cursor, statement, params, context, executemany):  # noqa: D401
        start = getattr(context, "_query_start_time", None)
        if start is None:
            return
        dur_s = time.perf_counter() - start
        if (dur_s * 1000.0) >= thr:
            log = get_logger("sql.slow")
            sql_head = (statement or "").strip().split("\n", 1)[0]
            log.warning(
                "slow_query",
                duration_ms=round(dur_s * 1000.0, 2),
                sql_head=sql_head[:512],
                rows_affected=getattr(cursor, "rowcount", None),
            )
            _metrics_on_slow_sql(dur_s)


# ---------- Bridge to DB query stats ----------
def get_query_stats_bridge() -> dict:
    try:
        from app.core.db import get_query_stats  # type: ignore
        return get_query_stats()
    except Exception:
        return {}


# ---------- Structlog metrics processor (placeholder for future extension) ----------
class _StructlogMetricsProcessor:
    def __call__(self, _, __, event_dict):
        level = event_dict.get("level", "") or event_dict.get("log_level", "")
        if level:
            _metrics_on_log(str(level).upper())
        return event_dict


def _attach_structlog_metrics() -> None:
    # текущая реализация держит метрики через _MetricsHandler; здесь оставлен
    # хук для возможной ре-конфигурации structlog pipeline, если понадобится.
    return


# ---------- setup all ----------
def setup_logging_fullstack() -> None:
    """
    Полная настройка:
      - setup_logging() / configure_logging()
      - integrate_uvicorn_loggers()
      - attach log metrics handler
      - log_startup_summary()
    Вызывайте в main.py на старте.
    """
    setup_logging()
    integrate_uvicorn_loggers()
    _attach_metrics_handler()
    _attach_structlog_metrics()
    log_startup_summary()


__all__ = [
    "setup_logging",
    "configure_logging",  # alias
    "setup_logging_fullstack",
    "get_logger",
    "bind_context",       # functional setter
    "bound_context",      # contextmanager
    "clear_context",
    "enrich_from_request",
    "set_level_for",
    "log_startup_summary",
    "AuditLogger",
    "audit_logger",
    "LoggingContextMiddleware",
    "enable_sqlalchemy_slow_query_logging",
    "get_query_stats_bridge",
    "redact_secrets",
]
