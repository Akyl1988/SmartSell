from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import FastAPI

_SECRET_KEYS = ("SECRET", "PASSWORD", "TOKEN", "KEY", "PASS", "PRIVATE", "CREDENTIAL", "AUTH")
_REDACT_KEYS_EXACT = {
    "DATABASE_URL",
    "DB_URL",
    "REDIS_URL",
    "SQLALCHEMY_DATABASE_URI",
}


def env_truthy(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on", "enable", "enabled")


def env_int(name: str, default: int = 0) -> int:
    try:
        return int(os.getenv(name, str(default)) or default)
    except Exception:
        return default


def is_postgres_url(url: str | None) -> bool:
    if not url:
        return False
    value = (url or "").lower()
    return value.startswith("postgres://") or value.startswith("postgresql://")


def env_last_deploy_time() -> str:
    for key in ("LAST_DEPLOY_AT", "LAST_DEPLOY_TIME", "DEPLOYED_AT", "DEPLOY_TIME"):
        value = os.getenv(key)
        if value:
            return value
    return ""


def parse_trusted_hosts() -> list[str] | None:
    raw = os.getenv("TRUSTED_HOSTS", "")
    if not raw:
        return None
    return [host.strip() for host in raw.split(",") if host.strip()]


def redact(value: Any) -> Any:
    try:
        if value is None:
            return None
        string_value = str(value)
        if not string_value:
            return string_value
        if len(string_value) <= 6:
            return "***"
        return string_value[:2] + "…" + string_value[-2:]
    except Exception:
        return "***"


def redact_dict(data: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in data.items():
        key_upper = str(key).upper()
        if key_upper in _REDACT_KEYS_EXACT or any(part in key_upper for part in _SECRET_KEYS):
            out[key] = redact(value)
        else:
            out[key] = value
    return out


def has_path_prefix(app: FastAPI, prefix: str) -> bool:
    """Проверяет, что в приложении уже есть хотя бы один маршрут на указанный префикс."""
    try:
        for route in app.router.routes:
            route_path = getattr(route, "path", None) or getattr(route, "path_format", None)
            if isinstance(route_path, str) and route_path.startswith(prefix):
                return True
    except Exception:
        pass
    return False


async def run_lifespan_startup(
    *,
    logger: logging.Logger,
    settings: Any,
    core_config: Any,
    validate_prod_secrets_fn: Callable[[Any], None],
    should_disable_startup_hooks_fn: Callable[[], bool],
    check_provider_registry_fn: Callable[[], Awaitable[dict[str, dict[str, Any]]]],
    run_startup_side_effects_fn: Callable[[Any], None],
    import_models_once_fn: Callable[[], None],
    bootstrap_feature_flags_from_env_fn: Callable[[], None],
    configure_base_logging_fn: Callable[[], None],
    global_state: dict[str, Any],
) -> Any:
    configure_base_logging_fn()
    logger.info("Application startup… env=%s version=%s", settings.ENVIRONMENT, settings.VERSION)
    try:
        validate_prod_secrets_fn(settings)
    except Exception as e:
        logger.error("startup secret validation failed: %s", e)
        raise
    disable_hooks = should_disable_startup_hooks_fn()
    if disable_hooks:
        logger.info("Startup hooks disabled (tests/CI flag)")
    try:
        startup_require_raw = os.getenv("STARTUP_REQUIRE_PROVIDERS")
        require_providers = env_truthy(startup_require_raw, settings.is_production)
        should_validate_providers = (
            settings.is_production and require_providers and ((not disable_hooks) or (startup_require_raw is not None))
        )
        if should_validate_providers:
            providers = await check_provider_registry_fn()
            otp = providers.get("otp") if isinstance(providers, dict) else None
            if otp and not otp.get("ok", False):
                raise RuntimeError("otp_provider_not_configured")
            messaging = providers.get("messaging") if isinstance(providers, dict) else None
            if messaging and not messaging.get("ok", False):
                raise RuntimeError("email_provider_not_configured")
            payments = providers.get("payments") if isinstance(providers, dict) else None
            if payments and not payments.get("ok", False):
                raise RuntimeError("payment_provider_not_configured")
    except Exception as e:
        logger.error("startup provider validation failed: %s", e)
        raise
    try:
        run_startup_side_effects_fn(settings)
    except Exception as e:
        logger.warning("startup side effects failed: %s", e)
    try:
        env_val = str(getattr(settings, "ENVIRONMENT", "") or "").lower()
        if not core_config._under_pytest() and env_val != "local" and getattr(settings, "DB_URL_SOURCE", "") == "DEFAULT":
            raise RuntimeError("DATABASE_URL is required for non-local environments")
    except Exception as e:
        if isinstance(e, RuntimeError):
            if isinstance(providers, dict):
                otp = providers.get("otp")
                messaging = providers.get("messaging")
                if otp and not otp.get("ok", False):
                    raise RuntimeError("otp_provider_not_configured")
                if messaging and not messaging.get("ok", False):
                    raise RuntimeError("email_provider_not_configured")
    import_models_once_fn()
    try:
        from app.dev.seed import ensure_dev_seed

        await ensure_dev_seed()
    except Exception as e:
        logger.warning("dev seed skipped: %s", e)
    try:
        from app.api.routes import get_mount_diagnostics as _get_mount_diagnostics

        diag = _get_mount_diagnostics()
        logger.info(
            "router_mount_diagnostics",
            extra={
                "event_name": "router_mount_diagnostics",
                "counts": diag.get("counts", {}),
                "module_timings_ms": diag.get("module_timings_ms", {}),
            },
        )
    except Exception:
        pass

    try:
        settings.init_opentelemetry()
    except Exception as e:
        logger.info("settings.init_opentelemetry failed or disabled: %s", e)

    bootstrap_feature_flags_from_env_fn()

    try:
        from app.api.v1 import campaigns as _  # noqa: F401

        logger.info("Campaigns module detected and ready")
    except Exception as e:
        logger.info("Campaigns module not loaded: %s", e)

    try:
        role = getattr(settings, "PROCESS_ROLE", os.getenv("PROCESS_ROLE", "web")) or "web"
        enable_scheduler = env_truthy(os.getenv("ENABLE_SCHEDULER", "0")) or getattr(settings, "ENABLE_SCHEDULER", False)
        background_tasks_enabled = env_truthy(os.getenv("SMARTSELL_BACKGROUND_TASKS", "1"), True)
        allowed_roles = {"web", "worker", "scheduler"}
        if role not in allowed_roles:
            logger.info("Scheduler start skipped for role", extra={"role": role, "enable_scheduler": enable_scheduler})
        elif not background_tasks_enabled:
            logger.info(
                "Scheduler start skipped: SMARTSELL_BACKGROUND_TASKS=0",
                extra={"role": role, "enable_scheduler": enable_scheduler},
            )
        elif disable_hooks:
            logger.info("Scheduler start skipped: startup hooks disabled")
        elif not enable_scheduler:
            logger.info("Scheduler start skipped: ENABLE_SCHEDULER=False")
        elif global_state.get("scheduler_started"):
            logger.info("Scheduler already started")
        else:
            try:
                from app.worker import scheduler_worker  # type: ignore
            except ImportError as e:
                logger.warning("Scheduler start skipped: APScheduler not installed (%s)", e)
            else:
                scheduler_worker.start()
                global_state["scheduler_started"] = True
                logger.info("APScheduler worker started (ENABLE_SCHEDULER=True)")
    except Exception as e:
        logger.error("Scheduler start failed: %s", e)

    kaspi_sync_task = None
    try:
        role = getattr(settings, "PROCESS_ROLE", os.getenv("PROCESS_ROLE", "web")) or "web"
        enable_kaspi_sync = env_truthy(os.getenv("ENABLE_KASPI_SYNC_RUNNER", "0"))
        if role not in ("web", "runner"):
            logger.info(
                "Kaspi sync runner start skipped for role", extra={"role": role, "enable_kaspi_sync": enable_kaspi_sync}
            )
        elif disable_hooks:
            logger.info("Kaspi sync runner start skipped: startup hooks disabled")
        elif not enable_kaspi_sync:
            logger.info("Kaspi sync runner start skipped: ENABLE_KASPI_SYNC_RUNNER=False")
        elif global_state.get("kaspi_sync_started"):
            logger.info("Kaspi sync runner already started")
        else:
            from app.services.kaspi_orders_sync_runner import run_kaspi_orders_sync_once

            async def _kaspi_sync_loop():
                interval_seconds = int(os.getenv("KASPI_SYNC_INTERVAL_SECONDS", "300"))
                logger.info("kaspi_sync_runner: background task started", interval_seconds=interval_seconds)
                while True:
                    try:
                        await run_kaspi_orders_sync_once()
                    except Exception as exc:
                        logger.error("kaspi_sync_runner: unexpected error in loop", error=str(exc), exc_info=True)
                    await asyncio.sleep(interval_seconds)

            kaspi_sync_task = asyncio.create_task(_kaspi_sync_loop())
            global_state["kaspi_sync_task"] = kaspi_sync_task
            global_state["kaspi_sync_started"] = True
            logger.info("Kaspi orders sync runner started (ENABLE_KASPI_SYNC_RUNNER=1)")
    except Exception as e:
        logger.error("Kaspi sync runner start failed: %s", e)

    return kaspi_sync_task


async def run_lifespan_shutdown(*, logger: logging.Logger, global_state: dict[str, Any], kaspi_sync_task: Any) -> None:
    if kaspi_sync_task and not kaspi_sync_task.done():
        kaspi_sync_task.cancel()
        try:
            await kaspi_sync_task
        except asyncio.CancelledError:
            pass
        logger.info("Kaspi sync runner stopped")

    try:
        client = global_state.get("httpx")
        if client is not None:
            try:
                await client.aclose()
            except Exception:
                pass
            global_state["httpx"] = None
    except Exception:
        pass

    try:
        redis_client = global_state.get("redis")
        if redis_client is not None:
            try:
                if hasattr(redis_client, "aclose"):
                    await redis_client.aclose()
                else:
                    await redis_client.close()
            except Exception:
                pass
            global_state["redis"] = None
    except Exception:
        pass

    try:
        engine = global_state.get("db_engine")
        if engine is not None:
            try:
                engine.dispose()
            except Exception:
                pass
            global_state["db_engine"] = None
    except Exception:
        pass

    try:
        global_state["celery"] = None
    except Exception:
        pass

    try:
        from app.core.provider_registry import ProviderRegistry

        await ProviderRegistry.shutdown()
    except Exception:
        pass

    try:
        if global_state.get("scheduler_started"):
            try:
                from app.worker import scheduler_worker  # type: ignore
            except ImportError:
                logger.warning("Scheduler stop skipped: APScheduler not installed")
            else:
                scheduler_worker.stop()
                global_state["scheduler_started"] = False
                logger.info("APScheduler worker stopped")
    except Exception as e:
        logger.error("Scheduler stop failed: %s", e)

    logger.info("Application shutdown complete.")
