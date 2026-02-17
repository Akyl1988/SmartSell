from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.core import config as config_mod
from app.core.exceptions import SmartSellValidationError
from app.models.company import Company
from app.models.product import Product
from app.models.repricing import RepricingRule, RepricingRunItem
from app.models.user import User
from app.services.repricing import apply_repricing_run_to_kaspi, run_reprcing_for_company

pytestmark = pytest.mark.asyncio


def _get_user_by_phone(db_session, phone: str) -> User:
    return db_session.query(User).filter(User.phone == phone).one()


async def _create_product(async_db_session, company_id: int, *, price: Decimal, sku: str) -> Product:
    suffix = uuid.uuid4().hex[:6]
    product = Product(
        company_id=company_id,
        name=f"Product {suffix}",
        slug=f"product-{suffix}",
        sku=sku,
        price=price,
        min_price=Decimal("10.00"),
        max_price=Decimal("200.00"),
        is_active=True,
    )
    async_db_session.add(product)
    await async_db_session.commit()
    await async_db_session.refresh(product)
    return product


def _rule_payload() -> dict:
    return {
        "name": f"rule-{uuid.uuid4().hex[:6]}",
        "enabled": True,
        "is_active": True,
        "scope_type": "all",
        "step": "5.00",
        "rounding_mode": "nearest",
    }


async def _create_run(async_client, headers) -> int:
    created = await async_client.post(
        "/api/v1/repricing/rules",
        json=_rule_payload(),
        headers=headers,
    )
    assert created.status_code == 201, created.text

    run_resp = await async_client.post("/api/v1/repricing/run", headers=headers)
    assert run_resp.status_code == 200, run_resp.text
    run_id = run_resp.json().get("run_id")
    assert run_id
    return run_id


async def test_repricing_apply_dry_run_does_not_call_kaspi(
    async_client,
    db_session,
    async_db_session,
    company_a_admin_headers,
    monkeypatch,
):
    user_a = _get_user_by_phone(db_session, "+70000010001")
    await _create_product(async_db_session, user_a.company_id, price=Decimal("100.00"), sku="SKU-DRY")

    async def _raise_apply(**_kwargs):
        raise AssertionError("apply_price_updates should not be called")

    monkeypatch.setattr("app.integrations.marketplaces.kaspi.pricing.apply_price_updates", _raise_apply)

    run_id = await _create_run(async_client, company_a_admin_headers)

    apply_resp = await async_client.post(
        f"/api/v1/repricing/runs/{run_id}/apply",
        params={"dry_run": True},
        headers=company_a_admin_headers,
    )
    assert apply_resp.status_code == 200, apply_resp.text
    payload = apply_resp.json()
    items = payload.get("items") or []
    assert any(item.get("status") == "dry_run" for item in items)


async def test_repricing_apply_dry_run_is_idempotent(
    async_client,
    db_session,
    async_db_session,
    company_a_admin_headers,
):
    user_a = _get_user_by_phone(db_session, "+70000010001")
    await _create_product(async_db_session, user_a.company_id, price=Decimal("90.00"), sku="SKU-IDEMP")

    run_id = await _create_run(async_client, company_a_admin_headers)

    apply_resp = await async_client.post(
        f"/api/v1/repricing/runs/{run_id}/apply",
        params={"dry_run": True},
        headers=company_a_admin_headers,
    )
    assert apply_resp.status_code == 200, apply_resp.text

    first_items = (
        (
            await async_db_session.execute(
                select(RepricingRunItem).where(
                    RepricingRunItem.run_id == run_id,
                    RepricingRunItem.status == "dry_run",
                )
            )
        )
        .scalars()
        .all()
    )
    first_count = len(first_items)
    assert first_count > 0

    apply_resp = await async_client.post(
        f"/api/v1/repricing/runs/{run_id}/apply",
        params={"dry_run": True},
        headers=company_a_admin_headers,
    )
    assert apply_resp.status_code == 200, apply_resp.text

    second_items = (
        (
            await async_db_session.execute(
                select(RepricingRunItem).where(
                    RepricingRunItem.run_id == run_id,
                    RepricingRunItem.status == "dry_run",
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(second_items) == first_count


async def test_repricing_apply_calls_kaspi_and_is_tenant_safe(
    async_client,
    db_session,
    async_db_session,
    company_a_admin_headers,
    company_b_admin_headers,
    monkeypatch,
):
    user_a = _get_user_by_phone(db_session, "+70000010001")
    product = await _create_product(async_db_session, user_a.company_id, price=Decimal("120.00"), sku="SKU-OK")

    async def _apply_mock(**_kwargs):
        return [{"product_id": product.id, "ok": True}]

    monkeypatch.setattr("app.integrations.marketplaces.kaspi.pricing.apply_price_updates", _apply_mock)

    run_id = await _create_run(async_client, company_a_admin_headers)

    apply_resp = await async_client.post(
        f"/api/v1/repricing/runs/{run_id}/apply",
        headers=company_a_admin_headers,
    )
    assert apply_resp.status_code == 200, apply_resp.text
    payload = apply_resp.json()
    items = payload.get("items") or []
    assert any(item.get("status") == "ok" for item in items)

    forbidden = await async_client.post(
        f"/api/v1/repricing/runs/{run_id}/apply",
        headers=company_b_admin_headers,
    )
    assert forbidden.status_code == 404, forbidden.text


async def test_repricing_apply_missing_kaspi_creds_returns_422(
    async_db_session,
    factory,
    monkeypatch,
):
    company = await factory["create_company"]()
    await _create_product(async_db_session, company.id, price=Decimal("110.00"), sku="SKU-NOAPI")

    rule = RepricingRule(
        company_id=company.id,
        name="rule-missing-kaspi",
        enabled=True,
        is_active=True,
        scope_type="all",
        step=Decimal("5.00"),
        rounding_mode="nearest",
    )
    async_db_session.add(rule)
    await async_db_session.commit()

    run = await run_reprcing_for_company(async_db_session, company.id)
    await async_db_session.commit()
    await async_db_session.refresh(run)
    run_id = run.id
    assert run_id

    company = await async_db_session.get(Company, company.id)
    assert company is not None
    company.kaspi_api_key = None
    await async_db_session.commit()

    monkeypatch.delenv("KASPI_API_TOKEN", raising=False)
    monkeypatch.delenv("KASPI_TOKEN", raising=False)
    monkeypatch.delenv("KASPI_SHOP_TOKEN", raising=False)
    monkeypatch.delenv("KASPI_API_URL", raising=False)

    config_mod.get_settings.cache_clear()
    refreshed = config_mod.get_settings()
    monkeypatch.setattr(config_mod, "settings", refreshed, raising=False)
    for key, value in refreshed.model_dump().items():
        setattr(config_mod.settings, key, value)

    monkeypatch.setattr(config_mod.settings, "KASPI_API_TOKEN", "", raising=False)
    monkeypatch.setattr(config_mod.settings, "KASPI_API_URL", "", raising=False)
    assert config_mod.settings.KASPI_API_TOKEN == ""
    assert config_mod.settings.KASPI_API_URL == ""

    with pytest.raises(SmartSellValidationError) as exc:
        await apply_repricing_run_to_kaspi(
            async_db_session,
            run_id=run_id,
            company_id=company.id,
            dry_run=False,
            api_key=None,
            base_url=None,
            resolve_credentials=False,
        )

    assert exc.value.code == "KASPI_NOT_CONFIGURED"
    assert exc.value.http_status == 422

    apply_items = (
        (
            await async_db_session.execute(
                select(RepricingRunItem).where(
                    RepricingRunItem.run_id == run_id,
                    RepricingRunItem.reason.in_({"apply", "apply_failed", "dry_run", "missing_mapping"}),
                )
            )
        )
        .scalars()
        .all()
    )
    assert not apply_items


async def test_repricing_apply_records_per_item_errors(
    async_client,
    db_session,
    async_db_session,
    company_a_admin_headers,
    monkeypatch,
):
    user_a = _get_user_by_phone(db_session, "+70000010001")
    product_ok = await _create_product(async_db_session, user_a.company_id, price=Decimal("130.00"), sku="SKU-OK2")
    product_bad = await _create_product(
        async_db_session,
        user_a.company_id,
        price=Decimal("140.00"),
        sku="SKU-BAD",
    )

    async def _apply_mock(**_kwargs):
        return [
            {"product_id": product_ok.id, "ok": True},
            {"product_id": product_bad.id, "ok": False, "error": "bad sku"},
        ]

    monkeypatch.setattr("app.integrations.marketplaces.kaspi.pricing.apply_price_updates", _apply_mock)

    run_id = await _create_run(async_client, company_a_admin_headers)

    apply_resp = await async_client.post(
        f"/api/v1/repricing/runs/{run_id}/apply",
        headers=company_a_admin_headers,
    )
    assert apply_resp.status_code == 200, apply_resp.text
    payload = apply_resp.json()
    assert payload.get("status") == "failed"
    items = payload.get("items") or []
    assert any(item.get("status") == "failed" for item in items)
    assert payload.get("last_error")
