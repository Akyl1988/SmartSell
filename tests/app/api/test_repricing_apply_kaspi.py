from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models.product import Product
from app.models.user import User

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
