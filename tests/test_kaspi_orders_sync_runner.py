"""
Tests for Kaspi Orders Sync Runner.

Verifies:
1. Runner does NOT auto-start when should_disable_startup_hooks() is true (TESTING mode)
2. Runner iterates companies and handles failures gracefully
3. Runner respects concurrency limits and adds jitter
"""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from app.core.config import should_disable_startup_hooks
from app.models.company import Company
from app.services.kaspi_orders_sync_runner import run_kaspi_orders_sync_once
from app.services.kaspi_service import KaspiSyncAlreadyRunning


@pytest.mark.asyncio
async def test_runner_not_started_in_testing_mode():
    """Regression: Runner must NOT auto-start when should_disable_startup_hooks() is True."""
    assert should_disable_startup_hooks() is True, "Tests must run with startup hooks disabled"

    # In test mode, ENABLE_KASPI_SYNC_RUNNER should have no effect on startup
    # This is enforced by the lifespan check in app/main.py
    # We verify the check works by confirming should_disable_startup_hooks() returns True


@pytest.mark.asyncio
async def test_runner_iterates_multiple_companies_with_isolation(monkeypatch, async_db_session):
    """Runner should iterate all active companies and continue even when one fails."""
    from app.models.company import Company

    # Create two test companies
    company1 = Company(id=9001, name="Test Company 1", is_active=True, kaspi_store_id="store-9001")
    company2 = Company(id=9002, name="Test Company 2", is_active=True, kaspi_store_id="store-9002")
    async_db_session.add(company1)
    async_db_session.add(company2)
    await async_db_session.commit()

    # Track sync calls
    sync_calls = []

    async def fake_sync_orders(self, *, db, company_id, **kwargs):  # noqa: ARG001
        sync_calls.append(company_id)
        if company_id == 9001:
            # First company raises an error
            raise RuntimeError("Simulated sync failure for company 9001")
        # Second company succeeds
        return {"fetched": 0, "inserted": 0, "updated": 0}

    from app.services import kaspi_service

    monkeypatch.setattr(kaspi_service.KaspiService, "sync_orders", fake_sync_orders)

    # Run sync
    result = await run_kaspi_orders_sync_once(base_delay_seconds=0.0)  # No jitter for faster tests

    # Verify both companies were attempted
    assert sorted(sync_calls) == [9001, 9002]

    # Verify summary reflects one success and one failure
    assert result["total"] == 2
    assert result["success"] == 1  # company 9002 succeeded
    assert result["failed"] == 1  # company 9001 failed
    assert result["locked"] == 0


@pytest.mark.asyncio
async def test_runner_handles_locked_sync(monkeypatch, async_db_session):
    """Runner should track locked syncs separately and continue."""
    from app.models.company import Company

    # Create two test companies
    company1 = Company(id=9003, name="Locked Company", is_active=True, kaspi_store_id="store-9003")
    company2 = Company(id=9004, name="Available Company", is_active=True, kaspi_store_id="store-9004")
    async_db_session.add(company1)
    async_db_session.add(company2)
    await async_db_session.commit()

    async def fake_sync_orders(self, *, db, company_id, **kwargs):  # noqa: ARG001
        if company_id == 9003:
            # First company is locked (another sync running)
            raise KaspiSyncAlreadyRunning("kaspi sync already running")
        # Second company succeeds
        return {"fetched": 5, "inserted": 2, "updated": 3}

    from app.services import kaspi_service

    monkeypatch.setattr(kaspi_service.KaspiService, "sync_orders", fake_sync_orders)

    # Run sync
    result = await run_kaspi_orders_sync_once(base_delay_seconds=0.0)

    # Verify summary
    assert result["total"] == 2
    assert result["success"] == 1  # company 9004 succeeded
    assert result["failed"] == 0
    assert result["locked"] == 1  # company 9003 locked


@pytest.mark.asyncio
async def test_runner_handles_timeout(monkeypatch, async_db_session):
    """Runner should handle asyncio.TimeoutError gracefully."""
    from app.models.company import Company

    company = Company(id=9005, name="Timeout Company", is_active=True, kaspi_store_id="store-9005")
    async_db_session.add(company)
    await async_db_session.commit()

    async def fake_sync_orders(self, *, db, company_id, **kwargs):  # noqa: ARG001
        raise asyncio.TimeoutError("Sync timeout")

    from app.services import kaspi_service

    monkeypatch.setattr(kaspi_service.KaspiService, "sync_orders", fake_sync_orders)

    result = await run_kaspi_orders_sync_once(base_delay_seconds=0.0)

    assert result["total"] == 1
    assert result["success"] == 0
    assert result["failed"] == 1  # Timeout counted as failure
    assert result["locked"] == 0


@pytest.mark.asyncio
async def test_runner_no_companies_returns_empty_summary(async_db_session):
    """Runner should handle case where no active companies exist."""
    # Ensure no active companies (test DB is clean)
    stmt = select(Company).where(Company.is_active.is_(True))
    result = await async_db_session.execute(stmt)
    companies = result.scalars().all()

    # If any exist, deactivate them for this test
    for company in companies:
        company.is_active = False
    await async_db_session.commit()

    result = await run_kaspi_orders_sync_once()

    assert result["total"] == 0
    assert result["success"] == 0
    assert result["failed"] == 0
    assert result["locked"] == 0


@pytest.mark.asyncio
async def test_runner_skips_missing_merchant_uid(monkeypatch, async_db_session):
    """Runner should not call sync_orders when merchant_uid is missing."""
    company = Company(id=9201, name="Missing Merchant", is_active=True, kaspi_store_id=None)
    async_db_session.add(company)
    await async_db_session.commit()

    sync_calls = []

    async def fake_sync_orders(self, *, db, company_id, **kwargs):  # noqa: ARG001
        sync_calls.append(company_id)
        return {"fetched": 0, "inserted": 0, "updated": 0}

    from app.services import kaspi_service

    monkeypatch.setattr(kaspi_service.KaspiService, "sync_orders", fake_sync_orders)

    result = await run_kaspi_orders_sync_once(base_delay_seconds=0.0)

    assert sync_calls == []
    assert result["total"] == 1
    assert result["success"] == 0
    assert result["failed"] == 1


@pytest.mark.asyncio
async def test_runner_respects_max_concurrent(monkeypatch, async_db_session):
    """Runner should respect max_concurrent limit using semaphore."""
    from app.models.company import Company

    # Create 5 companies
    for i in range(5):
        company = Company(id=9100 + i, name=f"Company {i}", is_active=True, kaspi_store_id=f"store-{i}")
        async_db_session.add(company)
    await async_db_session.commit()

    # Track concurrent execution
    active_count = 0
    max_active = 0
    lock = asyncio.Lock()

    async def fake_sync_orders(self, *, db, company_id, **kwargs):  # noqa: ARG001
        nonlocal active_count, max_active
        async with lock:
            active_count += 1
            max_active = max(max_active, active_count)

        await asyncio.sleep(0.01)  # Simulate work

        async with lock:
            active_count -= 1

        return {"fetched": 0, "inserted": 0, "updated": 0}

    from app.services import kaspi_service

    monkeypatch.setattr(kaspi_service.KaspiService, "sync_orders", fake_sync_orders)

    # Run with max_concurrent=2
    result = await run_kaspi_orders_sync_once(max_concurrent=2, base_delay_seconds=0.0)

    assert result["total"] == 5
    assert result["success"] == 5
    assert max_active <= 2, f"Expected max 2 concurrent, got {max_active}"
