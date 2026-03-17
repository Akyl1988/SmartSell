from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from app.models.company import Company
from app.models.kaspi_order_sync_state import KaspiOrderSyncState
from app.services.kaspi_service import DEFAULT_KASPI_ORDER_STATES, KaspiService


@pytest.mark.asyncio
async def test_kaspi_service_sync_orders_timeout_returns_result(async_db_session, monkeypatch):
    service = KaspiService(api_key="token", base_url="https://kaspi.kz")
    service._sync_timeout_seconds = 0.01

    async def _iter_orders_pages(*args, **kwargs):
        await asyncio.sleep(0.05)
        if False:
            yield []

    monkeypatch.setattr(service, "_iter_orders_pages", _iter_orders_pages)

    result = await service.sync_orders(
        db=async_db_session,
        company_id=1001,
        request_id="req-1",
    )

    assert result["ok"] is False
    assert result["status"] == "timeout"


@pytest.mark.asyncio
async def test_kaspi_service_sync_orders_uses_env_state_list(async_db_session, monkeypatch):
    service = KaspiService(api_key="token", base_url="https://kaspi.kz")
    states: list[str | None] = []

    company = await async_db_session.get(Company, 1001)
    if company is None:
        company = Company(id=1001, name="Company 1001", kaspi_store_id="store-a")
        async_db_session.add(company)
        await async_db_session.commit()

    async def _iter_orders_pages(*, state=None, **kwargs):  # noqa: ANN001, ARG001
        states.append(state)
        if False:
            yield []

    monkeypatch.setenv("KASPI_ORDERS_SYNC_STATES", "all")
    monkeypatch.setattr(service, "_iter_orders_pages", _iter_orders_pages)

    result = await service.sync_orders(
        db=async_db_session,
        company_id=1001,
        request_id="req-states",
        max_pages=1,
    )

    assert result["ok"] is True
    assert states == list(DEFAULT_KASPI_ORDER_STATES)


@pytest.mark.asyncio
async def test_kaspi_service_sync_orders_clamps_stale_window_to_14_days(async_db_session, monkeypatch):
    service = KaspiService(api_key="token", base_url="https://kaspi.kz")
    captured: dict[str, datetime] = {}

    company = await async_db_session.get(Company, 1001)
    if company is None:
        company = Company(id=1001, name="Company 1001", kaspi_store_id="store-a")
        async_db_session.add(company)

    stale_synced_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=24)
    state = await async_db_session.get(KaspiOrderSyncState, 1001)
    if state is None:
        state = KaspiOrderSyncState(company_id=1001, last_synced_at=stale_synced_at)
        async_db_session.add(state)
    else:
        state.last_synced_at = stale_synced_at
    await async_db_session.commit()

    async def _iter_orders_pages(*, date_from, date_to, **kwargs):  # noqa: ANN001, ARG001
        captured["date_from"] = date_from
        captured["date_to"] = date_to
        if False:
            yield []

    monkeypatch.setattr(service, "_iter_orders_pages", _iter_orders_pages)

    result = await service.sync_orders(
        db=async_db_session,
        company_id=1001,
        request_id="req-window-clamp",
        statuses=["APPROVED_BY_BANK"],
    )

    assert result["ok"] is True
    assert "date_from" in captured and "date_to" in captured
    assert captured["date_to"] - captured["date_from"] <= timedelta(days=14)


@pytest.mark.asyncio
async def test_kaspi_service_sync_orders_clamps_backfill_to_14_days(async_db_session, monkeypatch):
    service = KaspiService(api_key="token", base_url="https://kaspi.kz")
    captured: dict[str, datetime] = {}

    company = await async_db_session.get(Company, 1001)
    if company is None:
        company = Company(id=1001, name="Company 1001", kaspi_store_id="store-a")
        async_db_session.add(company)
        await async_db_session.commit()

    async def _iter_orders_pages(*, date_from, date_to, **kwargs):  # noqa: ANN001, ARG001
        captured["date_from"] = date_from
        captured["date_to"] = date_to
        if False:
            yield []

    monkeypatch.setattr(service, "_iter_orders_pages", _iter_orders_pages)

    result = await service.sync_orders(
        db=async_db_session,
        company_id=1001,
        request_id="req-backfill-clamp",
        statuses=["APPROVED_BY_BANK"],
        backfill_days=30,
    )

    assert result["ok"] is True
    assert "date_from" in captured and "date_to" in captured
    assert captured["date_to"] - captured["date_from"] <= timedelta(days=14)
