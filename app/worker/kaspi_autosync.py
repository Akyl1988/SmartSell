"""
Kaspi orders auto-sync background job.

Periodically syncs orders for all eligible companies (those with Kaspi integration configured).
Uses per-company advisory locks for safe concurrent execution.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import settings
from app.core.logging import get_logger
from app.models.company import Company
from app.services.kaspi_service import KaspiService, KaspiSyncAlreadyRunning

logger = get_logger(__name__)


# Global state for last run summary
_last_run_summary: dict[str, Any] = {
    "started_at": None,
    "finished_at": None,
    "eligible_companies": 0,
    "success": 0,
    "failed": 0,
    "locked": 0,
    "errors": [],
}


def get_last_run_summary() -> dict[str, Any]:
    """Get summary of last auto-sync run."""
    return _last_run_summary.copy()


def _utcnow() -> datetime:
    """Return naive UTC datetime."""
    return datetime.utcnow()


async def _get_eligible_companies(db: AsyncSession) -> list[int]:
    """
    Get list of company IDs that have Kaspi integration enabled.

    A company is eligible if:
    - is_active = True
    - deleted_at IS NULL
    - kaspi_store_id IS NOT NULL (has Kaspi configured)
    """
    stmt = select(Company.id).where(
        and_(
            Company.is_active.is_(True),
            Company.deleted_at.is_(None),
            Company.kaspi_store_id.isnot(None),
        )
    )
    result = await db.execute(stmt)
    company_ids = [row[0] for row in result.all()]
    logger.info("Kaspi auto-sync: found %d eligible companies", len(company_ids))
    return company_ids


async def _sync_company(company_id: int, db: AsyncSession) -> dict[str, Any]:
    """
    Sync orders for a single company.

    Returns:
        dict with status: 'success', 'locked', or 'failed'
    """
    try:
        svc = KaspiService()
        result = await svc.sync_orders(
            db=db,
            company_id=company_id,
            request_id=f"autosync-{company_id}",
        )
        logger.info(
            "Kaspi auto-sync: company_id=%d success fetched=%d inserted=%d updated=%d",
            company_id,
            result.get("fetched", 0),
            result.get("inserted", 0),
            result.get("updated", 0),
        )
        return {"company_id": company_id, "status": "success", "result": result}
    except KaspiSyncAlreadyRunning:
        logger.warning("Kaspi auto-sync: company_id=%d locked (skipped)", company_id)
        return {"company_id": company_id, "status": "locked"}
    except Exception as exc:
        logger.error("Kaspi auto-sync: company_id=%d failed: %s", company_id, exc, exc_info=True)
        return {"company_id": company_id, "status": "failed", "error": str(exc)}


async def _sync_companies_batch(company_ids: list[int]) -> list[dict[str, Any]]:
    """
    Sync orders for a batch of companies concurrently.

    Respects KASPI_AUTOSYNC_MAX_CONCURRENCY setting.
    """
    max_concurrency = settings.KASPI_AUTOSYNC_MAX_CONCURRENCY
    results = []

    # Create async engine for background task
    from app.core.config import resolve_async_database_url

    async_url, source, pg_dsn = resolve_async_database_url(settings)
    engine = create_async_engine(
        async_url,
        echo=False,
        pool_pre_ping=True,
    )
    AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    try:
        # Process in chunks to respect concurrency limit
        for i in range(0, len(company_ids), max_concurrency):
            batch = company_ids[i : i + max_concurrency]
            logger.info(
                "Kaspi auto-sync: processing batch %d-%d of %d companies (concurrency=%d)",
                i + 1,
                min(i + max_concurrency, len(company_ids)),
                len(company_ids),
                len(batch),
            )

            # Create tasks for this batch
            tasks = []
            for company_id in batch:

                async def sync_with_session(cid: int):
                    async with AsyncSessionLocal() as session:
                        return await _sync_company(cid, session)

                tasks.append(sync_with_session(company_id))

            # Run batch concurrently
            batch_results = await asyncio.gather(*tasks, return_exceptions=True)

            # Handle exceptions from gather
            for idx, result in enumerate(batch_results):
                if isinstance(result, Exception):
                    company_id = batch[idx]
                    logger.error(
                        "Kaspi auto-sync: company_id=%d unexpected error: %s",
                        company_id,
                        result,
                        exc_info=result,
                    )
                    results.append({"company_id": company_id, "status": "failed", "error": str(result)})
                else:
                    results.append(result)
    finally:
        await engine.dispose()

    return results


async def run_kaspi_autosync_async() -> dict[str, Any]:
    """
    Main entry point for Kaspi orders auto-sync job.

    Returns summary dict with counts of success/failed/locked.
    """
    global _last_run_summary

    started_at = _utcnow()
    logger.info("Kaspi auto-sync job started")

    # Reset summary
    _last_run_summary = {
        "started_at": started_at.isoformat(),
        "finished_at": None,
        "eligible_companies": 0,
        "success": 0,
        "failed": 0,
        "locked": 0,
        "errors": [],
    }

    # Create async engine for querying companies
    engine = create_async_engine(
        settings.DATABASE_URL_ASYNC,
        echo=False,
        pool_pre_ping=True,
    )
    AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    try:
        # Get eligible companies
        async with AsyncSessionLocal() as session:
            company_ids = await _get_eligible_companies(session)

        _last_run_summary["eligible_companies"] = len(company_ids)

        if not company_ids:
            logger.info("Kaspi auto-sync: no eligible companies found")
            finished_at = _utcnow()
            _last_run_summary["finished_at"] = finished_at.isoformat()
            return _last_run_summary

        # Sync companies
        results = await _sync_companies_batch(company_ids)

        # Count results
        for result in results:
            status = result.get("status", "failed")
            if status == "success":
                _last_run_summary["success"] += 1
            elif status == "locked":
                _last_run_summary["locked"] += 1
            else:
                _last_run_summary["failed"] += 1
                error_msg = f"company_id={result.get('company_id')}: {result.get('error', 'unknown')}"
                _last_run_summary["errors"].append(error_msg)

        finished_at = _utcnow()
        _last_run_summary["finished_at"] = finished_at.isoformat()

        logger.info(
            "Kaspi auto-sync job completed: eligible=%d success=%d failed=%d locked=%d duration=%.2fs",
            _last_run_summary["eligible_companies"],
            _last_run_summary["success"],
            _last_run_summary["failed"],
            _last_run_summary["locked"],
            (finished_at - started_at).total_seconds(),
        )

        return _last_run_summary

    except Exception as exc:
        logger.error("Kaspi auto-sync job failed: %s", exc, exc_info=True)
        finished_at = _utcnow()
        _last_run_summary["finished_at"] = finished_at.isoformat()
        _last_run_summary["errors"].append(f"Job error: {exc}")
        raise
    finally:
        await engine.dispose()


def run_kaspi_autosync() -> dict[str, Any]:
    """
    Synchronous wrapper for Kaspi auto-sync job.

    For use with APScheduler which doesn't support async directly.
    """
    try:
        # Try to get existing event loop
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # If loop is running, create a new one (shouldn't happen in APScheduler context)
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
    except RuntimeError:
        # No event loop, create one
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    try:
        return loop.run_until_complete(run_kaspi_autosync_async())
    finally:
        # Don't close the loop if it was already running
        pass
