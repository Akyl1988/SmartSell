from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models.product import Product
from app.models.repricing import RepricingDiff, RepricingRun
from app.models.user import User

pytestmark = pytest.mark.asyncio


def _get_user_by_phone(db_session, phone: str) -> User:
    return db_session.query(User).filter(User.phone == phone).one()


async def _create_product(async_db_session, company_id: int, *, price: Decimal) -> Product:
    suffix = uuid.uuid4().hex[:6]
    product = Product(
        company_id=company_id,
        name=f"Product {suffix}",
        slug=f"product-{suffix}",
        sku=f"SKU{suffix.upper()}",
        price=price,
        min_price=Decimal("10.00"),
        max_price=Decimal("200.00"),
        is_active=True,
    )
    async_db_session.add(product)
    await async_db_session.commit()
    await async_db_session.refresh(product)
    return product


async def _create_rule(async_client, headers, **overrides):
    payload = {
        "name": f"rule-{uuid.uuid4().hex[:6]}",
        "enabled": True,
        "is_active": True,
        "step": "5.00",
        "undercut": "5.00",
        "cooldown_seconds": 0,
        "max_delta_percent": "20.00",
    }
    payload.update(overrides)
    created = await async_client.post("/api/v1/pricing/rules", json=payload, headers=headers)
    assert created.status_code == 201, created.text
    return created.json()


async def test_pricing_preview_apply(
    async_client,
    db_session,
    async_db_session,
    company_a_admin_headers,
    company_b_admin_headers,
):
    user_a = _get_user_by_phone(db_session, "+70000010001")
    user_b = _get_user_by_phone(db_session, "+70000020001")

    product_a = await _create_product(async_db_session, user_a.company_id, price=Decimal("100.00"))
    product_b = await _create_product(async_db_session, user_b.company_id, price=Decimal("150.00"))

    created = await _create_rule(async_client, company_a_admin_headers)
    rule_id = created.get("id")

    preview = await async_client.post(
        "/api/v1/pricing/preview",
        json={"rule_id": rule_id},
        headers=company_a_admin_headers,
    )
    assert preview.status_code == 200, preview.text
    preview_items = preview.json()
    assert any(item.get("product_id") == product_a.id for item in preview_items)
    assert all(item.get("product_id") != product_b.id for item in preview_items)

    apply_resp = await async_client.post(
        "/api/v1/pricing/apply",
        json={"rule_id": rule_id},
        headers=company_a_admin_headers,
    )
    assert apply_resp.status_code == 200, apply_resp.text
    apply_payload = apply_resp.json()
    assert apply_payload.get("run_id")
    diffs = apply_payload.get("diffs") or []
    assert any(item.get("product_id") == product_a.id for item in diffs)

    await async_db_session.refresh(product_a)
    await async_db_session.refresh(product_b)
    assert product_a.price == Decimal("95.00")
    assert product_b.price == Decimal("150.00")

    run = await async_db_session.execute(select(RepricingRun).where(RepricingRun.id == apply_payload["run_id"]))
    assert run.scalar_one_or_none() is not None

    diffs_rows = (
        (await async_db_session.execute(select(RepricingDiff).where(RepricingDiff.company_id == user_a.company_id)))
        .scalars()
        .all()
    )
    assert any(diff.product_id == product_a.id for diff in diffs_rows)

    forbidden = await async_client.post(
        "/api/v1/pricing/apply",
        json={"rule_id": rule_id},
        headers=company_b_admin_headers,
    )
    assert forbidden.status_code == 404, forbidden.text


async def test_pricing_apply_cooldown_blocks_second_run(
    async_client,
    db_session,
    async_db_session,
    company_a_admin_headers,
):
    user_a = _get_user_by_phone(db_session, "+70000010001")
    product = await _create_product(async_db_session, user_a.company_id, price=Decimal("100.00"))

    rule = await _create_rule(async_client, company_a_admin_headers, cooldown_seconds=3600)
    rule_id = rule.get("id")

    first = await async_client.post(
        "/api/v1/pricing/apply",
        json={"rule_id": rule_id},
        headers=company_a_admin_headers,
    )
    assert first.status_code == 200, first.text

    second = await async_client.post(
        "/api/v1/pricing/apply",
        json={"rule_id": rule_id},
        headers=company_a_admin_headers,
    )
    assert second.status_code == 200, second.text

    await async_db_session.refresh(product)
    assert product.price == Decimal("95.00")
    second_items = second.json().get("diffs") or []
    cooldown = next(item for item in second_items if item.get("product_id") == product.id)
    assert cooldown.get("new_price") is None
    assert cooldown.get("reason") == "cooldown"


async def test_pricing_scope_filters(
    async_client,
    db_session,
    async_db_session,
    company_a_admin_headers,
):
    user_a = _get_user_by_phone(db_session, "+70000010001")

    product_a = await _create_product(async_db_session, user_a.company_id, price=Decimal("120.00"))
    product_b = await _create_product(async_db_session, user_a.company_id, price=Decimal("130.00"))
    product_c = await _create_product(async_db_session, user_a.company_id, price=Decimal("140.00"))

    rule_ids = await _create_rule(
        async_client,
        company_a_admin_headers,
        scope={"type": "product_ids", "product_ids": [product_a.id, product_b.id]},
    )
    preview_ids = await async_client.post(
        "/api/v1/pricing/preview",
        json={"rule_id": rule_ids.get("id")},
        headers=company_a_admin_headers,
    )
    assert preview_ids.status_code == 200, preview_ids.text
    ids_items = preview_ids.json()
    ids = {item.get("product_id") for item in ids_items}
    assert product_a.id in ids
    assert product_b.id in ids
    assert product_c.id not in ids

    apply_ids = await async_client.post(
        "/api/v1/pricing/apply",
        json={"rule_id": rule_ids.get("id")},
        headers=company_a_admin_headers,
    )
    assert apply_ids.status_code == 200, apply_ids.text
    await async_db_session.refresh(product_a)
    await async_db_session.refresh(product_b)
    await async_db_session.refresh(product_c)
    assert product_a.price == Decimal("115.00")
    assert product_b.price == Decimal("125.00")
    assert product_c.price == Decimal("140.00")

    rule_skus = await _create_rule(
        async_client,
        company_a_admin_headers,
        scope={"type": "sku_list", "sku_list": [product_c.sku]},
    )
    preview_skus = await async_client.post(
        "/api/v1/pricing/preview",
        json={"rule_id": rule_skus.get("id")},
        headers=company_a_admin_headers,
    )
    assert preview_skus.status_code == 200, preview_skus.text
    sku_items = preview_skus.json()
    assert {item.get("product_id") for item in sku_items} == {product_c.id}


async def test_pricing_bounds_and_max_delta(
    async_client,
    db_session,
    async_db_session,
    company_a_admin_headers,
):
    user_a = _get_user_by_phone(db_session, "+70000010001")
    product = await _create_product(async_db_session, user_a.company_id, price=Decimal("100.00"))
    product.min_price = Decimal("98.00")
    await async_db_session.commit()
    await async_db_session.refresh(product)

    rule = await _create_rule(
        async_client,
        company_a_admin_headers,
        undercut="20.00",
        step="20.00",
        max_delta_percent="5.00",
    )

    applied = await async_client.post(
        "/api/v1/pricing/apply",
        json={"rule_id": rule.get("id")},
        headers=company_a_admin_headers,
    )
    assert applied.status_code == 200, applied.text

    await async_db_session.refresh(product)
    assert product.price == Decimal("98.00")
