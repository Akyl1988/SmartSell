from __future__ import annotations

from datetime import datetime

import pytest
import sqlalchemy as sa

from app.core.config import settings
from app.models.company import Company
from app.models.integration_event import IntegrationEvent
from app.models.order import Order, OrderSource, OrderStatus, OrderStatusHistory
from app.services.kaspi_service import KaspiService


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict | None = None, headers: dict | None = None):
        self.status_code = status_code
        self._payload = payload or {}
        self.headers = headers or {}

    def json(self):
        return self._payload


async def _ensure_company(async_db_session, company_id: int, store_id: str) -> None:
    company = await async_db_session.get(Company, company_id)
    if company is None:
        company = Company(id=company_id, name=f"Company {company_id}", kaspi_store_id=store_id)
        async_db_session.add(company)
    elif not company.kaspi_store_id:
        company.kaspi_store_id = store_id
    await async_db_session.commit()


async def _create_order(async_db_session, *, company_id: int, external_id: str) -> Order:
    order = Order(
        company_id=company_id,
        order_number=f"KASPI-{company_id}-{external_id}",
        external_id=external_id,
        source=OrderSource.KASPI,
        status=OrderStatus.PENDING,
        total_amount=100,
        currency="KZT",
    )
    async_db_session.add(order)
    await async_db_session.commit()
    await async_db_session.refresh(order)
    return order


@pytest.mark.asyncio
async def test_accept_updates_order_and_event(async_client, async_db_session, monkeypatch, company_a_admin_headers):
    await _ensure_company(async_db_session, 1001, "store-a")
    order = await _create_order(async_db_session, company_id=1001, external_id="ext-1")

    async def fake_request(self, **kwargs):  # noqa: ANN001
        return _FakeResponse(200, payload={"data": {"attributes": {"state": "ACCEPTED"}}})

    monkeypatch.setattr(KaspiService, "_orders_http_request", fake_request)

    resp = await async_client.post(
        "/api/v1/kaspi/orders/ext-1/accept?merchantUid=store-a",
        headers=company_a_admin_headers,
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["ok"] is True
    assert data["code"] == "ok"

    await async_db_session.refresh(order)
    assert order.status == OrderStatus.CONFIRMED

    history = (
        (await async_db_session.execute(sa.select(OrderStatusHistory).where(OrderStatusHistory.order_id == order.id)))
        .scalars()
        .all()
    )
    assert any(entry.new_status == OrderStatus.CONFIRMED for entry in history)

    events = (
        (
            await async_db_session.execute(
                sa.select(IntegrationEvent).where(
                    IntegrationEvent.company_id == 1001,
                    IntegrationEvent.kind == "kaspi_order_action",
                )
            )
        )
        .scalars()
        .all()
    )
    assert any(event.meta_json and event.meta_json.get("action") == "accept" for event in events)


@pytest.mark.asyncio
async def test_cancel_updates_order(async_client, async_db_session, monkeypatch, company_a_admin_headers):
    await _ensure_company(async_db_session, 1001, "store-a")
    order = await _create_order(async_db_session, company_id=1001, external_id="ext-2")

    async def fake_request(self, **kwargs):  # noqa: ANN001
        return _FakeResponse(200, payload={"status": "CANCELLED"})

    monkeypatch.setattr(KaspiService, "_orders_http_request", fake_request)

    resp = await async_client.post(
        "/api/v1/kaspi/orders/ext-2/cancel?merchantUid=store-a",
        headers=company_a_admin_headers,
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["ok"] is True

    await async_db_session.refresh(order)
    assert order.status == OrderStatus.CANCELLED


@pytest.mark.asyncio
async def test_action_tenant_isolation(async_client, async_db_session, monkeypatch, company_b_admin_headers):
    await _ensure_company(async_db_session, 1001, "store-a")
    await _ensure_company(async_db_session, 2001, "store-b")
    await _create_order(async_db_session, company_id=1001, external_id="ext-3")

    async def fake_request(self, **kwargs):  # noqa: ANN001
        raise AssertionError("Unexpected upstream call")

    monkeypatch.setattr(KaspiService, "_orders_http_request", fake_request)

    resp = await async_client.post(
        "/api/v1/kaspi/orders/ext-3/accept?merchantUid=store-b",
        headers=company_b_admin_headers,
    )
    assert resp.status_code == 404
    assert resp.json().get("code") == "order_not_found"


@pytest.mark.asyncio
async def test_action_rate_limited(async_client, async_db_session, monkeypatch, company_a_admin_headers):
    await _ensure_company(async_db_session, 1001, "store-a")
    await _create_order(async_db_session, company_id=1001, external_id="ext-4")

    async def fake_request(self, **kwargs):  # noqa: ANN001
        return _FakeResponse(429, payload={}, headers={"Retry-After": "7"})

    monkeypatch.setattr(KaspiService, "_orders_http_request", fake_request)

    resp = await async_client.post(
        "/api/v1/kaspi/orders/ext-4/accept?merchantUid=store-a",
        headers=company_a_admin_headers,
    )
    assert resp.status_code == 429
    data = resp.json()
    assert data["ok"] is False
    assert data["code"] == "rate_limited"
    assert data.get("retry_after") == 7


@pytest.mark.asyncio
async def test_actions_url_build(monkeypatch):
    captured: dict[str, str | None] = {"url": None}

    async def fake_request(self, *, url, **kwargs):  # noqa: ANN001
        captured["url"] = url
        return _FakeResponse(200, payload={"status": "ACCEPTED"})

    monkeypatch.setattr(KaspiService, "_orders_http_request", fake_request)
    monkeypatch.setattr(settings, "KASPI_ORDERS_ACTIONS_BASE_URL", "https://kaspi.test/shop/api")
    monkeypatch.setattr(settings, "KASPI_ORDERS_ACCEPT_PATH", "/v2/orders/{external_id}/accept")

    svc = KaspiService(api_key="token")
    result = await svc.accept_order(external_id="ext-5", merchant_uid="store-a", request_id="req-1")
    assert result["ok"] is True
    assert captured["url"] == "https://kaspi.test/shop/api/v2/orders/ext-5/accept"
