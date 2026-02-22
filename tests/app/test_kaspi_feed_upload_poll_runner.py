from __future__ import annotations

from datetime import datetime

import pytest
from sqlalchemy import select

from app.integrations.kaspi_adapter import KaspiAdapterError
from app.models.company import Company
from app.models.kaspi_feed_export import KaspiFeedExport
from app.models.kaspi_feed_upload import KaspiFeedUpload
from app.models.marketplace import KaspiStoreToken
from app.worker.kaspi_feed_upload_poll import run_kaspi_feed_upload_poll_async


class _FakeAdapterUpload:
    def feed_upload(self, *args, **kwargs):
        return {"importCode": "IC-FEED-1", "status": "received"}

    def feed_import_status(self, *args, **kwargs):
        return {"importCode": "IC-FEED-1", "status": "done"}


class _FailingAdapter:
    def feed_upload(self, *args, **kwargs):
        raise KaspiAdapterError("upstream_unavailable")

    def feed_import_status(self, *args, **kwargs):
        raise KaspiAdapterError("upstream_unavailable")


@pytest.mark.asyncio
async def test_feed_upload_poll_uploads_pending(async_db_session, monkeypatch):
    company = Company(id=1001, name="Company 1001", kaspi_store_id="store-a")
    async_db_session.add(company)
    await async_db_session.commit()

    export = KaspiFeedExport(
        company_id=1001,
        kind="offers",
        format="xml",
        status="generated",
        checksum="abc",
        payload_text="<xml/>",
    )
    async_db_session.add(export)
    await async_db_session.commit()
    await async_db_session.refresh(export)

    upload = KaspiFeedUpload(
        company_id=1001,
        merchant_uid="M1",
        export_id=export.id,
        source="offers",
        status="pending",
        attempts=0,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    async_db_session.add(upload)
    await async_db_session.commit()

    async def _get_token(session, store_name: str):
        return "token-a"

    monkeypatch.setattr(KaspiStoreToken, "get_token", _get_token)
    from app.worker import kaspi_feed_upload_poll as poll_module

    monkeypatch.setattr(poll_module, "KaspiAdapter", lambda: _FakeAdapterUpload())

    summary = await run_kaspi_feed_upload_poll_async()
    assert summary["processed"] == 1

    refreshed = (
        (await async_db_session.execute(select(KaspiFeedUpload).where(KaspiFeedUpload.company_id == 1001)))
        .scalars()
        .first()
    )
    assert refreshed is not None
    await async_db_session.refresh(refreshed)
    assert refreshed.import_code == "IC-FEED-1"
    assert refreshed.status in {"received", "done", "success", "completed", "published"}


@pytest.mark.asyncio
async def test_feed_upload_poll_status_success(async_db_session, monkeypatch):
    company = Company(id=1002, name="Company 1002", kaspi_store_id="store-b")
    async_db_session.add(company)
    await async_db_session.commit()

    upload = KaspiFeedUpload(
        company_id=1002,
        merchant_uid="M2",
        export_id=None,
        source="offers",
        status="pending",
        import_code="IC-FEED-2",
        attempts=0,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    async_db_session.add(upload)
    await async_db_session.commit()

    async def _get_token(session, store_name: str):
        return "token-b"

    monkeypatch.setattr(KaspiStoreToken, "get_token", _get_token)
    from app.worker import kaspi_feed_upload_poll as poll_module

    monkeypatch.setattr(poll_module, "KaspiAdapter", lambda: _FakeAdapterUpload())

    summary = await run_kaspi_feed_upload_poll_async()
    assert summary["processed"] == 1

    refreshed = (
        (await async_db_session.execute(select(KaspiFeedUpload).where(KaspiFeedUpload.company_id == 1002)))
        .scalars()
        .first()
    )
    assert refreshed is not None
    await async_db_session.refresh(refreshed)
    assert refreshed.status in {"done", "success", "completed", "published"}
    assert refreshed.next_attempt_at is None


@pytest.mark.asyncio
async def test_feed_upload_poll_backoff_on_error(async_db_session, monkeypatch):
    company = Company(id=1003, name="Company 1003", kaspi_store_id="store-c")
    async_db_session.add(company)
    await async_db_session.commit()

    upload = KaspiFeedUpload(
        company_id=1003,
        merchant_uid="M3",
        export_id=None,
        source="offers",
        status="pending",
        import_code="IC-FEED-3",
        attempts=0,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    async_db_session.add(upload)
    await async_db_session.commit()

    async def _get_token(session, store_name: str):
        return "token-c"

    monkeypatch.setattr(KaspiStoreToken, "get_token", _get_token)
    from app.worker import kaspi_feed_upload_poll as poll_module

    monkeypatch.setattr(poll_module, "KaspiAdapter", lambda: _FailingAdapter())

    summary = await run_kaspi_feed_upload_poll_async()
    assert summary["processed"] == 1

    refreshed = (
        (await async_db_session.execute(select(KaspiFeedUpload).where(KaspiFeedUpload.company_id == 1003)))
        .scalars()
        .first()
    )
    assert refreshed is not None
    assert refreshed.status in {"pending", "failed"}
    if refreshed.status == "pending":
        assert refreshed.next_attempt_at is not None
