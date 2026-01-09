import pytest

from app.api.v1 import kaspi as kaspi_module


@pytest.mark.asyncio
async def test_kaspi_orders_sync_allows_empty_body(monkeypatch, async_client, company_a_admin_headers):
    called = {"count": 0, "company_id": None}

    class _FakeKaspiService:
        async def sync_orders(self, company_id: int, db, request_id=None):  # noqa: ANN001
            called["count"] += 1
            called["company_id"] = company_id
            return {"synced_for": company_id}

    monkeypatch.setattr(kaspi_module, "KaspiService", _FakeKaspiService)

    resp = await async_client.post("/api/v1/kaspi/orders/sync", headers=company_a_admin_headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["synced_for"] == 1001

    resp_empty = await async_client.post(
        "/api/v1/kaspi/orders/sync",
        headers=company_a_admin_headers,
        json={},
    )
    assert resp_empty.status_code == 200, resp_empty.text
    assert resp_empty.json()["synced_for"] == 1001

    assert called["count"] == 2
    assert called["company_id"] == 1001


@pytest.mark.asyncio
async def test_kaspi_feed_ignores_company_param(monkeypatch, async_client, company_a_admin_headers):
    captured = {"company_id": None}

    class _FakeKaspiService:
        async def generate_product_feed(self, company_id: int, db):  # noqa: ANN001
            captured["company_id"] = company_id
            return "<feed/>"

    monkeypatch.setattr(kaspi_module, "KaspiService", _FakeKaspiService)

    resp = await async_client.get(
        "/api/v1/kaspi/feed",
        headers=company_a_admin_headers,
        params={"company_id": 999},
    )
    assert resp.status_code == 200, resp.text
    assert resp.text == "<feed/>"
    assert captured["company_id"] == 1001


@pytest.mark.asyncio
async def test_kaspi_feed_propagates_errors(monkeypatch, async_client, company_a_admin_headers):
    class _FailingKaspiService:
        async def generate_product_feed(self, company_id: int, db):  # noqa: ANN001
            raise Exception("boom")

    monkeypatch.setattr(kaspi_module, "KaspiService", _FailingKaspiService)

    resp = await async_client.get("/api/v1/kaspi/feed", headers=company_a_admin_headers)

    assert resp.status_code == 500
    assert "boom" in resp.text
