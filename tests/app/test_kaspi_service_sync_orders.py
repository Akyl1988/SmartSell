from __future__ import annotations

import asyncio

import pytest

from app.services.kaspi_service import KaspiService


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
