"""
Kaspi Orders Sync Runner - Production-safe periodic sync orchestrator.

This module provides a runner that iterates over all active companies and
triggers sync_orders for each, with proper isolation, logging, and backoff.
"""

import asyncio
import random
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.db import _get_async_engine
from app.models.company import Company
from app.services.integration_events import record_integration_event
from app.services.kaspi_service import KaspiService, KaspiSyncAlreadyRunning

logger = structlog.get_logger(__name__)


async def run_kaspi_orders_sync_once(
    *,
    max_concurrent: int = 3,
    base_delay_seconds: float = 1.0,
    max_delay_seconds: float = 60.0,
) -> dict[str, Any]:
    """
    Run Kaspi orders sync for all active companies once.

    Isolation: Each company sync runs independently. If one fails, others continue.
    Backoff: Adds jitter to prevent thundering herd. Respects Retry-After when available.

    Args:
        max_concurrent: Maximum number of concurrent syncs (default: 3)
        base_delay_seconds: Base delay between company syncs for jitter (default: 1.0s)
        max_delay_seconds: Maximum jitter delay (default: 60.0s)

    Returns:
        Summary dict with counts: {success: int, failed: int, locked: int, total: int}
    """
    engine = _get_async_engine()
    session_maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    success_count = 0
    failed_count = 0
    locked_count = 0
    total_count = 0

    logger.info("kaspi_sync_runner: starting sync run")

    async with session_maker() as db:
        # Query all active companies
        stmt = (
            select(Company.id, Company.name, Company.kaspi_store_id)
            .where(Company.is_active.is_(True))
            .order_by(Company.id)
        )
        result = await db.execute(stmt)
        companies = result.all()

        if not companies:
            logger.info("kaspi_sync_runner: no active companies found")
            return {"success": 0, "failed": 0, "locked": 0, "total": 0}

        total_count = len(companies)
        logger.info("kaspi_sync_runner: found companies", count=total_count)

    # Process companies with concurrency limit
    semaphore = asyncio.Semaphore(max_concurrent)

    async def _sync_company(company_id: int, company_name: str, merchant_uid: str | None) -> None:
        nonlocal success_count, failed_count, locked_count

        async with semaphore:
            # Add jitter to spread load
            jitter = random.uniform(0, min(base_delay_seconds, max_delay_seconds))
            if jitter > 0:
                await asyncio.sleep(jitter)

            async with session_maker() as session:
                svc = KaspiService()
                try:
                    merchant_uid_value = (merchant_uid or "").strip()
                    if not merchant_uid_value:
                        logger.warning(
                            "kaspi_sync_runner: missing merchant_uid",
                            company_id=company_id,
                            company_name=company_name,
                        )
                        await record_integration_event(
                            session,
                            company_id=company_id,
                            merchant_uid=None,
                            kind="kaspi_orders_sync",
                            status="skipped",
                            error_code="missing_merchant_uid",
                            error_message="missing_merchant_uid",
                        )
                        failed_count += 1
                        return
                    result = await svc.sync_orders(
                        db=session,
                        company_id=company_id,
                        merchant_uid=merchant_uid_value,
                        request_id=f"kaspi-sync-runner-{company_id}",
                    )
                    result_ok = result.get("ok", True)
                    status_value = str(result.get("status") or "").lower()
                    code_value = str(result.get("code") or "").lower()

                    if result_ok:
                        logger.info(
                            "kaspi_sync_runner: sync success",
                            company_id=company_id,
                            company_name=company_name,
                            merchant_uid=merchant_uid_value,
                            fetched=result.get("fetched", 0),
                            inserted=result.get("inserted", 0),
                            updated=result.get("updated", 0),
                        )
                        await record_integration_event(
                            session,
                            company_id=company_id,
                            merchant_uid=merchant_uid_value,
                            kind="kaspi_orders_sync",
                            status="success",
                            meta_json={
                                "fetched": result.get("fetched", 0),
                                "inserted": result.get("inserted", 0),
                                "updated": result.get("updated", 0),
                                "watermark": result.get("watermark"),
                                "page_limit_hit": result.get("page_limit_hit"),
                                "window_truncated": result.get("window_truncated"),
                            },
                        )
                        success_count += 1
                        return

                    if status_value == "locked" or code_value in {"locked", "sync_locked"}:
                        logger.info(
                            "kaspi_sync_runner: sync locked",
                            company_id=company_id,
                            company_name=company_name,
                            merchant_uid=merchant_uid_value,
                        )
                        await record_integration_event(
                            session,
                            company_id=company_id,
                            merchant_uid=merchant_uid_value,
                            kind="kaspi_orders_sync",
                            status="skipped",
                            error_code="locked",
                            error_message="kaspi sync already running",
                        )
                        locked_count += 1
                        return

                    if status_value == "rate_limited" or code_value == "rate_limited":
                        retry_after = result.get("retry_after")
                        delay = max(base_delay_seconds, float(retry_after or base_delay_seconds))
                        delay = min(delay, max_delay_seconds)
                        logger.warning(
                            "kaspi_sync_runner: rate limited",
                            company_id=company_id,
                            company_name=company_name,
                            merchant_uid=merchant_uid_value,
                            retry_after=retry_after,
                            backoff_seconds=delay,
                        )
                        await record_integration_event(
                            session,
                            company_id=company_id,
                            merchant_uid=merchant_uid_value,
                            kind="kaspi_orders_sync",
                            status="failed",
                            error_code="rate_limited",
                            error_message="kaspi rate limited",
                        )
                        failed_count += 1
                        if delay > 0:
                            await asyncio.sleep(delay)
                        return

                    logger.warning(
                        "kaspi_sync_runner: sync failed",
                        company_id=company_id,
                        company_name=company_name,
                        merchant_uid=merchant_uid_value,
                        status=status_value or None,
                        code=code_value or None,
                    )
                    await record_integration_event(
                        session,
                        company_id=company_id,
                        merchant_uid=merchant_uid_value,
                        kind="kaspi_orders_sync",
                        status="failed",
                        error_code=code_value or "failed",
                        error_message="kaspi orders sync failed",
                    )
                    failed_count += 1
                except KaspiSyncAlreadyRunning:
                    logger.info(
                        "kaspi_sync_runner: sync locked (concurrent run)",
                        company_id=company_id,
                        company_name=company_name,
                    )
                    await record_integration_event(
                        session,
                        company_id=company_id,
                        merchant_uid=merchant_uid_value,
                        kind="kaspi_orders_sync",
                        status="skipped",
                        error_code="locked",
                        error_message="kaspi sync already running",
                    )
                    locked_count += 1
                except asyncio.TimeoutError:
                    logger.warning(
                        "kaspi_sync_runner: sync timeout",
                        company_id=company_id,
                        company_name=company_name,
                    )
                    await record_integration_event(
                        session,
                        company_id=company_id,
                        merchant_uid=merchant_uid_value,
                        kind="kaspi_orders_sync",
                        status="failed",
                        error_code="timeout",
                        error_message="kaspi orders sync timeout",
                    )
                    failed_count += 1
                except Exception as exc:
                    logger.error(
                        "kaspi_sync_runner: sync failed",
                        company_id=company_id,
                        company_name=company_name,
                        error=str(exc),
                        exc_info=True,
                    )
                    await record_integration_event(
                        session,
                        company_id=company_id,
                        merchant_uid=merchant_uid_value,
                        kind="kaspi_orders_sync",
                        status="failed",
                        error_code="internal_error",
                        error_message=str(exc),
                    )
                    failed_count += 1

    # Launch all company syncs concurrently (semaphore limits actual concurrency)
    tasks = [
        _sync_company(company_id, company_name, merchant_uid) for company_id, company_name, merchant_uid in companies
    ]
    await asyncio.gather(*tasks, return_exceptions=True)

    summary = {
        "success": success_count,
        "failed": failed_count,
        "locked": locked_count,
        "total": total_count,
    }

    logger.info("kaspi_sync_runner: sync run complete", **summary)
    return summary
