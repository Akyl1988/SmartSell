from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import select

from app.models.company import Company
from app.models.integration_event import IntegrationEvent
from app.models.kaspi_feed_export import KaspiFeedExport
from app.models.kaspi_feed_upload import KaspiFeedUpload
from app.models.kaspi_offer import KaspiOffer
from app.models.marketplace import KaspiStoreToken


class _FakeKaspiAdapter:
    def __init__(self):
        self.last_upload_path: Path | None = None
        self.last_extra_env: dict[str, str] | None = None
        self.upload_calls = 0
        self.status_calls = 0

    def feed_upload(
        self,
        store: str,
        xml_path: str,
        comment: str | None = None,
        *,
        extra_env: dict[str, str] | None = None,
    ):
        self.upload_calls += 1
        self.last_upload_path = Path(xml_path)
        self.last_extra_env = extra_env
        assert store == "store-a"
        assert self.last_upload_path.is_file()
        content = self.last_upload_path.read_text(encoding="utf-8")
        assert "kaspi_catalog" in content
        return {"importCode": "IC-FEED-1", "status": "received"}

    def feed_import_status(
        self,
        store: str,
        import_id: str | None = None,
        *,
        extra_env: dict[str, str] | None = None,
    ):
        self.status_calls += 1
        self.last_extra_env = extra_env
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
    assert data["status"] == "received"
    assert data["source"] == "public_token"
    assert data["merchant_uid"] == "M123"
    assert data["comment"] == "test"
    assert data["attempts"] == 1
    assert fake_adapter.last_extra_env
    assert fake_adapter.last_extra_env.get("KASPI_FEED_TOKEN") == "token-a"
    assert "KASPI_FEED_UPLOAD_URL" in fake_adapter.last_extra_env
    assert "KASPI_FEED_STATUS_URL" in fake_adapter.last_extra_env
    assert "KASPI_FEED_RESULT_URL" in fake_adapter.last_extra_env

    upload_id = data["id"]

    refresh_resp = await async_client.post(
        f"/api/v1/kaspi/feed/uploads/{upload_id}/refresh",
        headers=company_a_admin_headers,
    )
    assert refresh_resp.status_code == 200
    refresh_data = refresh_resp.json()
    assert refresh_data["status"] == "done"
    assert fake_adapter.last_extra_env
    assert fake_adapter.last_extra_env.get("KASPI_FEED_TOKEN") == "token-a"
    assert "KASPI_FEED_STATUS_URL" in fake_adapter.last_extra_env

    events = (
        (
            await async_db_session.execute(
                select(IntegrationEvent).where(
                    IntegrationEvent.company_id == 1001,
                    IntegrationEvent.kind == "kaspi_feed",
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(events) >= 2
    assert any(event.meta_json and event.meta_json.get("import_code") == "IC-FEED-1" for event in events)


@pytest.mark.asyncio
async def test_kaspi_feed_upload_idempotent_by_request_id(
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

    headers = {**company_a_admin_headers, "X-Request-ID": "req-1"}
    resp1 = await async_client.post(
        "/api/v1/kaspi/feed/uploads",
        headers=headers,
        json={"merchant_uid": "M123", "source": "public_token"},
    )
    assert resp1.status_code == 200

    resp2 = await async_client.post(
        "/api/v1/kaspi/feed/uploads",
        headers=headers,
        json={"merchant_uid": "M123", "source": "public_token"},
    )
    assert resp2.status_code == 200
    assert resp1.json()["id"] == resp2.json()["id"]
    assert fake_adapter.upload_calls == 1

    uploads = (
        (await async_db_session.execute(select(KaspiFeedUpload).where(KaspiFeedUpload.company_id == 1001)))
        .scalars()
        .all()
    )
    assert len(uploads) == 1


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

    record = KaspiFeedUpload(
        company_id=1001,
        merchant_uid="M123",
        import_code="IC-OLD",
        status="uploaded",
        source="public_token",
    )
    async_db_session.add(record)
    await async_db_session.commit()
    await async_db_session.refresh(record)

    refresh_resp = await async_client.post(
        f"/api/v1/kaspi/feed/uploads/{record.id}/refresh",
        headers=company_a_manager_headers,
    )
    assert refresh_resp.status_code == 403


@pytest.mark.asyncio
async def test_upload_not_claimable_returns_409_and_existing_upload_id(
    async_client,
    async_db_session,
    company_a_admin_headers,
):
    await _ensure_company(async_db_session, 1001, "store-a")

    export = KaspiFeedExport(
        company_id=1001,
        kind="offers",
        format="xml",
        status="DONE",
        checksum="chk-1",
        payload_text="<xml>ok</xml>",
        stats_json={"merchant_uid": "M123"},
    )
    async_db_session.add(export)
    await async_db_session.commit()
    await async_db_session.refresh(export)

    existing = KaspiFeedUpload(
        company_id=1001,
        merchant_uid="M123",
        export_id=export.id,
        status="processing",
        source="export_id",
    )
    async_db_session.add(existing)
    await async_db_session.commit()
    await async_db_session.refresh(existing)

    resp = await async_client.post(
        f"/api/v1/kaspi/feeds/{export.id}/upload",
        headers=company_a_admin_headers,
    )
    assert resp.status_code == 409
    data = resp.json()
    assert data.get("detail") == "upload_not_claimable"
    assert data.get("code") == "HTTP_409"
    assert data.get("existing_upload_id") == str(existing.id)
    assert data.get("status") == "processing"


@pytest.mark.asyncio
async def test_invalid_upload_id_returns_422(async_client, company_a_admin_headers):
    resp = await async_client.post(
        "/api/v1/kaspi/feed/uploads/<PASTE_UPLOAD_ID_HERE>/refresh",
        headers=company_a_admin_headers,
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_kaspi_feed_upload_error_contract(async_client, company_a_admin_headers):
    headers = {**company_a_admin_headers, "X-Request-ID": "req-err-1"}
    resp = await async_client.post(
        "/api/v1/kaspi/feed/uploads",
        headers=headers,
        json={"merchant_uid": "M123", "source": "bad"},
    )
    assert resp.status_code == 400
    data = resp.json()
    assert data["detail"] == "invalid_source"
    assert data["code"] == "HTTP_400"
    assert data["request_id"] == "req-err-1"


@pytest.mark.asyncio
async def test_kaspi_feed_upload_publish(
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
        json={"merchant_uid": "M123", "source": "public_token"},
    )
    assert resp.status_code == 200
    upload_id = resp.json()["id"]

    publish_resp = await async_client.post(
        f"/api/v1/kaspi/feed/uploads/{upload_id}/publish",
        headers=company_a_admin_headers,
    )
    assert publish_resp.status_code == 200
    publish_data = publish_resp.json()
    assert publish_data["status"] == "published"
