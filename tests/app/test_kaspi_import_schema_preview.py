import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.company import Company
from app.models.kaspi_offer import KaspiOffer
from app.models.marketplace import KaspiStoreToken


async def _ensure_company_store(async_db_session: AsyncSession, company_id: int, store_id: str) -> None:
    company = await async_db_session.get(Company, company_id)
    if not company:
        company = Company(id=company_id, name=f"Company {company_id}", kaspi_store_id=store_id)
        async_db_session.add(company)
    else:
        company.kaspi_store_id = store_id
    await async_db_session.commit()


@pytest.mark.asyncio
async def test_kaspi_products_import_schema_endpoint(
    async_client, async_db_session, monkeypatch, company_a_admin_headers
):
    await _ensure_company_store(async_db_session, 1001, "store-a")

    async def _get_token(session, store_name: str):  # noqa: ARG001
        return "token-a"

    from app.services.kaspi_goods_import_client import KaspiGoodsImportClient

    schema_payload = {"required": ["sku", "name"], "fields": [{"name": "sku", "required": True}]}

    async def _get_schema(self):  # noqa: ANN001
        return schema_payload

    monkeypatch.setattr(KaspiStoreToken, "get_token", _get_token)
    monkeypatch.setattr(KaspiGoodsImportClient, "get_schema", _get_schema)

    resp = await async_client.get("/api/v1/kaspi/products/import/schema", headers=company_a_admin_headers)
    assert resp.status_code == 200
    assert resp.json() == schema_payload


@pytest.mark.asyncio
async def test_kaspi_offers_preview(async_client, async_db_session, company_a_admin_headers):
    await _ensure_company_store(async_db_session, 1001, "store-a")

    async_db_session.add(
        KaspiOffer(
            company_id=1001,
            merchant_uid="store-a",
            sku="SKU-PRV",
            title="Item Preview",
            price=1000,
            stock_count=2,
        )
    )
    await async_db_session.commit()

    resp = await async_client.get("/api/v1/kaspi/offers/preview?limit=1", headers=company_a_admin_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert len(data["items"]) == 1
    assert data["payload_hash"]


@pytest.mark.asyncio
async def test_kaspi_products_import_schema_validation(
    async_client, async_db_session, monkeypatch, company_a_admin_headers
):
    await _ensure_company_store(async_db_session, 1001, "store-a")

    async def _get_token(session, store_name: str):  # noqa: ARG001
        return "token-a"

    async_db_session.add(
        KaspiOffer(
            company_id=1001,
            merchant_uid="store-a",
            sku="SKU-VAL",
            title="Item Validate",
            price=1000,
            stock_count=2,
        )
    )
    await async_db_session.commit()

    from app.services.kaspi_goods_import_client import KaspiGoodsImportClient

    async def _get_schema(self):  # noqa: ANN001
        return {"required": ["brand"]}

    submit_calls = {"count": 0}

    async def _submit_import(self, payload_json: str):  # noqa: ANN001
        submit_calls["count"] += 1
        return {"importCode": "IC-VAL", "status": "UPLOADED"}

    monkeypatch.setattr(KaspiStoreToken, "get_token", _get_token)
    monkeypatch.setattr(KaspiGoodsImportClient, "get_schema", _get_schema)
    monkeypatch.setattr(KaspiGoodsImportClient, "submit_import", _submit_import)

    start = await async_client.post("/api/v1/kaspi/products/import/start", headers=company_a_admin_headers)
    import_code = start.json()["import_code"]

    resp = await async_client.post(
        f"/api/v1/kaspi/products/import/upload?i={import_code}", headers=company_a_admin_headers
    )
    assert resp.status_code == 422
    payload = resp.json()
    assert payload.get("code") == "schema_validation_failed"
    assert payload.get("errors")
    assert submit_calls["count"] == 0
