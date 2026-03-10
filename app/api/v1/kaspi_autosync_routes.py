from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field


class KaspiAutoSyncStatusOut(BaseModel):
    enabled: bool = Field(..., description="Включена ли автоматическая синхронизация")
    interval_minutes: int = Field(0, description="Интервал синхронизации в минутах")
    max_concurrency: int = Field(0, description="Максимум параллельных синхронизаций")
    runner_enabled: bool = Field(False, description="Включен ли main.py runner loop (ENABLE_KASPI_SYNC_RUNNER)")
    scheduler_job_effective_enabled: bool = Field(
        False, description="Включена ли APScheduler job после mutual exclusion"
    )
    job_registered: bool = Field(False, description="Зарегистрирована ли задача в scheduler")
    scheduler_running: bool | None = Field(None, description="Запущен ли scheduler (если доступно)")
    last_run_at: str | None = Field(None, description="ISO время последнего запуска")
    eligible_companies: int = Field(0, description="Сколько компаний подходят для синхронизации")
    success: int = Field(0, description="Успешно синхронизировано")
    locked: int = Field(0, description="Заблокировано (уже выполняется)")
    failed: int = Field(0, description="Неуспешно (ошибка)")


def register_kaspi_autosync_routes(
    router: APIRouter,
    *,
    require_store_admin_then_feature_fn: Callable[[str], Any],
    feature_kaspi_autosync: str,
    logger: Any,
) -> None:
    @router.get(
        "/autosync/status",
        summary="Статус автоматической синхронизации заказов",
        response_model=KaspiAutoSyncStatusOut,
    )
    async def kaspi_autosync_status(
        request: Request,
        current_user: Any = Depends(require_store_admin_then_feature_fn(feature_kaspi_autosync)),
    ):
        from app.core.config import settings

        enabled = getattr(settings, "KASPI_AUTOSYNC_ENABLED", False)
        interval_minutes = getattr(settings, "KASPI_AUTOSYNC_INTERVAL_MINUTES", 15)
        max_concurrency = getattr(settings, "KASPI_AUTOSYNC_MAX_CONCURRENCY", 3)

        runner_enabled = False
        scheduler_job_effective_enabled = False
        try:
            from app.worker.scheduler_worker import _env_truthy, should_register_kaspi_autosync

            runner_enabled = _env_truthy(os.getenv("ENABLE_KASPI_SYNC_RUNNER", "0"))
            scheduler_job_effective_enabled = should_register_kaspi_autosync()
        except ImportError as e:
            logger.debug(
                "scheduler_worker unavailable for autosync status",
                error=str(e),
                request_id=getattr(request.state, "request_id", None),
            )
        except Exception as e:
            logger.warning(
                "Failed to check scheduler_worker mutual exclusion state",
                error=str(e),
                request_id=getattr(request.state, "request_id", None),
            )

        job_registered = False
        scheduler_running = None
        try:
            from app.worker.scheduler_worker import scheduler

            scheduler_running = scheduler.running
            job = scheduler.get_job("kaspi_autosync")
            job_registered = job is not None
        except ImportError as e:
            logger.debug(
                "APScheduler not available for autosync status",
                error=str(e),
                request_id=getattr(request.state, "request_id", None),
            )
        except Exception as e:
            logger.warning(
                "Failed to get APScheduler job state",
                error=str(e),
                request_id=getattr(request.state, "request_id", None),
            )

        last_run_at = None
        eligible_companies = 0
        success = 0
        locked = 0
        failed = 0

        try:
            from app.worker.kaspi_autosync import get_last_run_summary

            summary = get_last_run_summary()
            last_run_at = summary.get("last_run_at")
            eligible_companies = summary.get("eligible_companies", 0)
            success = summary.get("success", 0)
            locked = summary.get("locked", 0)
            failed = summary.get("failed", 0)
        except ImportError as e:
            logger.debug(
                "kaspi_autosync module unavailable for last_run_summary",
                error=str(e),
                request_id=getattr(request.state, "request_id", None),
            )
        except Exception as e:
            logger.warning(
                "Failed to get kaspi_autosync last_run_summary",
                error=str(e),
                request_id=getattr(request.state, "request_id", None),
            )

        return KaspiAutoSyncStatusOut(
            enabled=enabled,
            interval_minutes=interval_minutes,
            max_concurrency=max_concurrency,
            runner_enabled=runner_enabled,
            scheduler_job_effective_enabled=scheduler_job_effective_enabled,
            job_registered=job_registered,
            scheduler_running=scheduler_running,
            last_run_at=last_run_at,
            eligible_companies=eligible_companies,
            success=success,
            locked=locked,
            failed=failed,
        )

    @router.post(
        "/autosync/trigger",
        summary="Ручной запуск автоматической синхронизации",
        response_model=KaspiAutoSyncStatusOut,
    )
    async def kaspi_autosync_trigger(
        current_user: Any = Depends(require_store_admin_then_feature_fn(feature_kaspi_autosync)),
    ):
        from app.core.config import settings

        enabled = getattr(settings, "KASPI_AUTOSYNC_ENABLED", False)

        if not enabled:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Kaspi auto-sync is disabled. Set KASPI_AUTOSYNC_ENABLED=true to enable.",
            )

        try:
            from app.worker.kaspi_autosync import run_kaspi_autosync

            run_kaspi_autosync()

            from app.worker.kaspi_autosync import get_last_run_summary

            summary = get_last_run_summary()
            return KaspiAutoSyncStatusOut(
                enabled=True,
                last_run_at=summary.get("last_run_at"),
                eligible_companies=summary.get("eligible_companies", 0),
                success=summary.get("success", 0),
                locked=summary.get("locked", 0),
                failed=summary.get("failed", 0),
            )
        except ImportError:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Kaspi auto-sync module not available",
            )
