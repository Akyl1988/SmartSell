from __future__ import annotations

import asyncio

import pytest

from app.models.company import Company
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
