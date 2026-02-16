from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models.product import Product
from app.models.subscription_catalog import Feature, Plan, PlanFeature
from app.models.user import User

pytestmark = pytest.mark.asyncio


def _get_user_by_phone(db_session, phone: str) -> User:
    return db_session.query(User).filter(User.phone == phone).one()


async def _create_product(async_db_session, company_id: int) -> Product:
    suffix = uuid.uuid4().hex[:6]
    product = Product(
        company_id=company_id,
        name=f"Preorder Product {suffix}",
        slug=f"preorder-product-{suffix}",
        sku=f"PRESKU{suffix.upper()}",
        price=Decimal("100.00"),
        is_active=True,
        is_preorder_enabled=True,
        preorder_until=int((datetime.utcnow() + timedelta(days=1)).timestamp()),
        preorder_deposit=Decimal("20.00"),
    )
    async_db_session.add(product)
    await async_db_session.commit()
    await async_db_session.refresh(product)
    return product


async def test_preorders_crud_and_rbac(
    async_client,
    db_session,
    async_db_session,
    company_a_admin_headers,
    company_b_admin_headers,
):
    user_a = _get_user_by_phone(db_session, "+70000010001")
    user_b = _get_user_by_phone(db_session, "+70000020001")

    product_a = await _create_product(async_db_session, user_a.company_id)
    await _create_product(async_db_session, user_b.company_id)

    created = await async_client.post(
        "/api/v1/preorders",
        json={
            "product_id": product_a.id,
            "qty": 2,
            "customer_name": "Alice",
            "customer_phone": "+77000000000",
            "comment": "call before delivery",
        },
        headers=company_a_admin_headers,
    )
    assert created.status_code == 201, created.text
    created_payload = created.json()
    preorder_id = created_payload.get("id")
    assert created_payload.get("company_id") == user_a.company_id

    listed = await async_client.get("/api/v1/preorders", headers=company_a_admin_headers)
    assert listed.status_code == 200, listed.text
    items = listed.json().get("items") or []
    assert any(item.get("id") == preorder_id for item in items)

    fetched = await async_client.get(f"/api/v1/preorders/{preorder_id}", headers=company_a_admin_headers)
    assert fetched.status_code == 200, fetched.text
    assert fetched.json().get("id") == preorder_id

    forbidden = await async_client.get(f"/api/v1/preorders/{preorder_id}", headers=company_b_admin_headers)
    assert forbidden.status_code == 404, forbidden.text

    other_list = await async_client.get("/api/v1/preorders", headers=company_b_admin_headers)
    assert other_list.status_code == 200, other_list.text
    other_items = other_list.json().get("items") or []
    assert all(item.get("company_id") == user_b.company_id for item in other_items)


async def test_preorders_respect_feature_limit(
    async_client,
    db_session,
    async_db_session,
    company_a_admin_headers,
):
    user_a = _get_user_by_phone(db_session, "+70000010001")

    plan_feature = (
        (
            await async_db_session.execute(
                select(PlanFeature)
                .join(Plan, Plan.id == PlanFeature.plan_id)
                .join(Feature, Feature.id == PlanFeature.feature_id)
                .where(Plan.code == "pro")
                .where(Feature.code == "preorders")
            )
        )
        .scalars()
        .first()
    )
    assert plan_feature is not None
    plan_feature.limits_json = {"max_preorders_per_period": 1}
    await async_db_session.commit()

    product = await _create_product(async_db_session, user_a.company_id)

    first = await async_client.post(
        "/api/v1/preorders",
        json={
            "product_id": product.id,
            "qty": 1,
            "customer_name": "Alice",
            "customer_phone": "+77000000000",
        },
        headers=company_a_admin_headers,
    )
    assert first.status_code == 201, first.text

    second = await async_client.post(
        "/api/v1/preorders",
        json={
            "product_id": product.id,
            "qty": 1,
            "customer_name": "Bob",
            "customer_phone": "+77000000001",
        },
        headers=company_a_admin_headers,
    )
    assert second.status_code == 402, second.text
    detail = second.json().get("detail")
    assert isinstance(detail, dict)
    assert detail.get("code") == "LIMIT_EXCEEDED"
