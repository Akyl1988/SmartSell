from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

from sqlalchemy import or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import resolve_async_database_url, settings
from app.core.logging import get_logger
from app.models.company import Company
from app.models.kaspi_import_run import KaspiImportRun
from app.models.marketplace import KaspiStoreToken
from app.services.kaspi_goods_import_client import (
    KaspiGoodsImportClient,
    KaspiImportNotAuthenticated,
    KaspiImportUpstreamError,
    KaspiImportUpstreamUnavailable,
)
from app.services.kaspi_import_run_utils import (
    classify_import_result,
    compute_next_poll_at,
    is_terminal_import_status,
    normalize_import_status,
)

logger = get_logger(__name__)

_KASPI_IMPORT_POLL_LOCK_KEY = 0x4B49504C  # "KIPL"


def _utcnow() -> datetime:
    return datetime.utcnow()


async def _try_poll_lock(session: AsyncSession) -> bool:
    res = await session.execute(text("SELECT pg_try_advisory_lock(:k)").bindparams(k=_KASPI_IMPORT_POLL_LOCK_KEY))
    return bool(res.scalar_one_or_none())


async def _release_poll_lock(session: AsyncSession) -> None:
    await session.execute(text("SELECT pg_advisory_unlock(:k)").bindparams(k=_KASPI_IMPORT_POLL_LOCK_KEY))


async def _fetch_due_runs(session: AsyncSession, *, limit: int) -> list[KaspiImportRun]:
    now = _utcnow()
    stmt = (
        select(KaspiImportRun)
        .where(KaspiImportRun.kaspi_import_code.isnot(None))
        .where(
            ~KaspiImportRun.status.in_(
                list({"FINISHED", "FINISHED_OK", "FINISHED_ERROR", "FAILED", "ERROR", "DONE", "COMPLETED", "DUPLICATE"})
            )
        )
        .where(or_(KaspiImportRun.next_poll_at.is_(None), KaspiImportRun.next_poll_at <= now))
        .order_by(KaspiImportRun.next_poll_at.asc().nullsfirst(), KaspiImportRun.created_at.asc())
        .limit(limit)
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def _poll_run(run_id: str, session_factory: sessionmaker) -> dict[str, Any]:
    async with session_factory() as session:
        run = await session.get(KaspiImportRun, run_id)
        if not run or not run.kaspi_import_code:
            return {"status": "skipped"}

        company = await session.get(Company, run.company_id)
        store_name = (company.kaspi_store_id or "").strip() if company else ""
        if not store_name:
            run.error_code = "kaspi_store_not_configured"
            run.error_message = "kaspi_store_not_configured"
            run.last_checked_at = _utcnow()
            run.attempts = int(run.attempts or 0) + 1
            run.next_poll_at = compute_next_poll_at(
                now=_utcnow(),
                status=run.status,
                attempts=run.attempts,
                base_delay_seconds=settings.KASPI_IMPORT_POLL_BACKOFF_BASE_SECONDS,
                max_delay_seconds=settings.KASPI_IMPORT_POLL_BACKOFF_MAX_SECONDS,
                result_payload=run.result_json,
            )
            await session.commit()
            return {"status": "failed", "error": "kaspi_store_not_configured"}

        token = await KaspiStoreToken.get_token(session, store_name)
        if not token:
            run.error_code = "kaspi_token_not_found"
            run.error_message = "kaspi_token_not_found"
            run.last_checked_at = _utcnow()
            run.attempts = int(run.attempts or 0) + 1
            run.next_poll_at = compute_next_poll_at(
                now=_utcnow(),
                status=run.status,
                attempts=run.attempts,
                base_delay_seconds=settings.KASPI_IMPORT_POLL_BACKOFF_BASE_SECONDS,
                max_delay_seconds=settings.KASPI_IMPORT_POLL_BACKOFF_MAX_SECONDS,
                result_payload=run.result_json,
            )
            await session.commit()
            return {"status": "failed", "error": "kaspi_token_not_found"}

        client = KaspiGoodsImportClient(token=token, base_url="https://kaspi.kz")
        now = _utcnow()
        try:
            status_response = await client.get_status(import_code=run.kaspi_import_code)
            status_value = str(status_response.get("status") or run.status)
            run.status = status_value
            run.status_json = status_response
            run.last_checked_at = now
            run.error_code = None
            run.error_message = None
            run.attempts = int(run.attempts or 0) + 1

            if is_terminal_import_status(status_value):
                result_response = await client.get_result(import_code=run.kaspi_import_code)
                run.result_json = result_response

            result_class, _summary = classify_import_result(run.status, run.result_json)
            if result_class == "failed":
                run.error_code = "import_failed"
                run.error_message = "kaspi_import_failed"
            else:
                run.error_code = None
                run.error_message = None

            run.next_poll_at = compute_next_poll_at(
                now=now,
                status=status_value,
                attempts=run.attempts,
                base_delay_seconds=settings.KASPI_IMPORT_POLL_BACKOFF_BASE_SECONDS,
                max_delay_seconds=settings.KASPI_IMPORT_POLL_BACKOFF_MAX_SECONDS,
                result_payload=run.result_json,
            )
            await session.commit()
            return {"status": "ok", "import_status": normalize_import_status(status_value)}
        except KaspiImportNotAuthenticated:
            run.error_code = "NOT_AUTHENTICATED"
            run.error_message = "NOT_AUTHENTICATED"
        except KaspiImportUpstreamUnavailable:
            run.error_code = "upstream_unavailable"
            run.error_message = "kaspi_upstream_unavailable"
        except KaspiImportUpstreamError:
            run.error_code = "upstream_error"
            run.error_message = "kaspi_upstream_error"
        except Exception as exc:  # pragma: no cover - defensive
            run.error_code = "poll_failed"
            run.error_message = str(exc)

        run.last_checked_at = now
        run.attempts = int(run.attempts or 0) + 1
        run.next_poll_at = compute_next_poll_at(
            now=now,
            status=run.status,
            attempts=run.attempts,
            base_delay_seconds=settings.KASPI_IMPORT_POLL_BACKOFF_BASE_SECONDS,
            max_delay_seconds=settings.KASPI_IMPORT_POLL_BACKOFF_MAX_SECONDS,
            result_payload=run.result_json,
        )
        await session.commit()
        return {"status": "failed", "error": run.error_code}


async def run_kaspi_import_poll_async() -> dict[str, Any]:
    summary: dict[str, Any] = {"polled": 0, "failed": 0, "skipped": 0, "locked": 0}

    async_url, _source, _fp = resolve_async_database_url(settings)
    engine = create_async_engine(async_url, echo=False, pool_pre_ping=True)
    AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    lock_session = AsyncSessionLocal()
    try:
        acquired = await _try_poll_lock(lock_session)
        if not acquired:
            summary["locked"] = 1
            return summary

        limit = int(settings.KASPI_IMPORT_POLL_BATCH_SIZE)
        runs = await _fetch_due_runs(lock_session, limit=limit)
        if not runs:
            return summary

        sem = asyncio.Semaphore(int(settings.KASPI_IMPORT_POLL_MAX_CONCURRENCY))

        async def _wrapped(run_id: str):
            async with sem:
                return await _poll_run(run_id, AsyncSessionLocal)

        tasks = [_wrapped(str(run.id)) for run in runs]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for result in results:
            if isinstance(result, Exception):
                summary["failed"] += 1
                continue
            status = result.get("status")
            if status == "ok":
                summary["polled"] += 1
            elif status == "skipped":
                summary["skipped"] += 1
            else:
                summary["failed"] += 1
        return summary
    finally:
        try:
            await _release_poll_lock(lock_session)
        except Exception:
            pass
        await lock_session.close()
        await engine.dispose()


def run_kaspi_import_poll() -> dict[str, Any]:
    """
    Synchronous wrapper for APScheduler.
    """
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    try:
        return loop.run_until_complete(run_kaspi_import_poll_async())
    finally:
        pass
