from __future__ import annotations

from pathlib import Path

import pytest

from app.models.company import Company
from app.models.kaspi_goods_import import KaspiGoodsImport
from app.models.kaspi_offer import KaspiOffer
from app.models.marketplace import KaspiStoreToken


class _FakeKaspiAdapter:
    def __init__(self):
        self.last_upload_path: Path | None = None

    def feed_upload(self, store: str, xml_path: str, comment: str | None = None):
        self.last_upload_path = Path(xml_path)
        assert store == "store-a"
        assert self.last_upload_path.is_file()
        content = self.last_upload_path.read_text(encoding="utf-8")
        assert "kaspi_catalog" in content
        return {"importCode": "IC-FEED-1", "status": "received"}

    def feed_import_status(self, store: str, import_id: str | None = None):
        assert store == "store-a"
        return {"importCode": import_id, "status": "done"}


async def _ensure_company(async_db_session, company_id: int, store_id: str) -> None:
    company = await async_db_session.get(Company, company_id)
    if not company:
        company = Company(id=company_id, name=f"Company {company_id}")
        async_db_session.add(company)
    company.kaspi_store_id = store_id
    await async_db_session.commit()


@pytest.mark.asyncio
async def test_kaspi_feed_upload_create_and_refresh(
    async_client,
    async_db_session,
    company_a_admin_headers,
    monkeypatch,
):
    await _ensure_company(async_db_session, 1001, "store-a")

    async def _get_token(session, store_name: str):
        return "token-a"

    monkeypatch.setattr(KaspiStoreToken, "get_token", _get_token)

    offer = KaspiOffer(
        company_id=1001,
        merchant_uid="M123",
        sku="SKU-1",
        title="Item 1",
        price=1000,
    )
    async_db_session.add(offer)
    await async_db_session.commit()

    fake_adapter = _FakeKaspiAdapter()
    from app.api.v1 import kaspi as kaspi_module

    monkeypatch.setattr(kaspi_module, "KaspiAdapter", lambda: fake_adapter)

    resp = await async_client.post(
        "/api/v1/kaspi/feed/uploads",
        headers=company_a_admin_headers,
        json={"merchant_uid": "M123", "source": "public_token", "comment": "test"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["import_code"] == "IC-FEED-1"
    assert data["status"] == "PENDING"
    assert data["source"] == "public_token"
    assert data["merchant_uid"] == "M123"

    upload_id = data["id"]

    refresh_resp = await async_client.post(
        f"/api/v1/kaspi/feed/uploads/{upload_id}/refresh-status",
        headers=company_a_admin_headers,
    )
    assert refresh_resp.status_code == 200
    refresh_data = refresh_resp.json()
    assert refresh_data["status"] == "done"
    assert refresh_data["status_json"]["status"] == "done"


@pytest.mark.asyncio
async def test_kaspi_feed_upload_permission_denied(
    async_client,
    async_db_session,
    company_a_manager_headers,
):
    await _ensure_company(async_db_session, 1001, "store-a")

    resp = await async_client.post(
        "/api/v1/kaspi/feed/uploads",
        headers=company_a_manager_headers,
        json={"merchant_uid": "M123", "source": "public_token"},
    )
    assert resp.status_code == 403

    record = KaspiGoodsImport(
        company_id=1001,
        merchant_uid="M123",
        import_code="IC-OLD",
        status="PENDING",
        source="public_token",
        request_json={},
    )
    async_db_session.add(record)
    await async_db_session.commit()
    await async_db_session.refresh(record)

    refresh_resp = await async_client.post(
        f"/api/v1/kaspi/feed/uploads/{record.id}/refresh-status",
        headers=company_a_manager_headers,
    )
    assert refresh_resp.status_code == 403
