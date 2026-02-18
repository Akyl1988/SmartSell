from __future__ import annotations

import json

import pytest
from sqlalchemy import select

from app.models.billing import Subscription
from app.models.company import Company
from app.models.product import Product
from app.models.warehouse import ProductStock, Warehouse
from app.services.preorder_policy import evaluate_preorder_state

pytestmark = pytest.mark.asyncio


async def _set_company_settings(async_db_session, company_id: int, settings: dict) -> None:
    company = await async_db_session.get(Company, company_id)
    if company is None:
        company = Company(id=company_id, name=f"Company {company_id}")
        async_db_session.add(company)
        await async_db_session.flush()
    company.settings = json.dumps(settings)
    await async_db_session.commit()


async def _seed_stock(async_db_session, *, company_id: int, product: Product, quantity: int) -> None:
    warehouse = Warehouse(company_id=company_id, name="Main", is_main=True)
    async_db_session.add(warehouse)
    await async_db_session.commit()
    await async_db_session.refresh(warehouse)

    stock = ProductStock(
        product_id=product.id,
        warehouse_id=warehouse.id,
        quantity=quantity,
        reserved_quantity=0,
    )
    async_db_session.add(stock)
    await async_db_session.commit()


async def _set_subscription_plan(async_db_session, *, company_id: int, plan: str) -> None:
    sub = (
        (
            await async_db_session.execute(
                select(Subscription).where(Subscription.company_id == company_id, Subscription.deleted_at.is_(None))
            )
        )
        .scalars()
        .first()
    )
    if sub is None:
        return
    sub.plan = plan
    await async_db_session.commit()


async def test_preorder_policy_tenant_isolation(async_db_session, factory):
    company_a = await factory["create_company"]()
    company_b = await factory["create_company"](name="Other")

    await _set_company_settings(
        async_db_session,
        company_a.id,
        {"preorders.auto_on_oos": True, "preorders.auto_on_oos_top_plan_only": False},
    )

    category_a = await factory["create_category"](name="Default A", slug="default-a")
    category_b = await factory["create_category"](name="Default B", slug="default-b")

    product_a = await factory["create_product"](company=company_a, category=category_a, stock_quantity=0)
    product_b = await factory["create_product"](company=company_b, category=category_b, stock_quantity=5)

    await _seed_stock(async_db_session, company_id=company_a.id, product=product_a, quantity=0)
    await _seed_stock(async_db_session, company_id=company_b.id, product=product_b, quantity=5)

    result = await evaluate_preorder_state(async_db_session, company_id=company_a.id, product_id=product_a.id)
    assert result["changed"] is True

    await async_db_session.refresh(product_a)
    await async_db_session.refresh(product_b)

    assert product_a.is_preorder_enabled is True
    assert product_b.is_preorder_enabled is False


async def test_preorder_policy_idempotent(async_db_session, factory):
    company = await factory["create_company"]()
    await _set_company_settings(
        async_db_session,
        company.id,
        {"preorders.auto_on_oos": True, "preorders.auto_on_oos_top_plan_only": False},
    )

    product = await factory["create_product"](company=company, stock_quantity=10)
    await _seed_stock(async_db_session, company_id=company.id, product=product, quantity=0)

    first = await evaluate_preorder_state(async_db_session, company_id=company.id, product_id=product.id)
    second = await evaluate_preorder_state(async_db_session, company_id=company.id, product_id=product.id)

    assert first["changed"] is True
    assert second["changed"] is False


async def test_preorder_policy_manual_override(async_db_session, factory):
    company = await factory["create_company"]()
    await _set_company_settings(async_db_session, company.id, {"preorders.auto_on_oos": True})

    product = await factory["create_product"](company=company, stock_quantity=5)
    await _seed_stock(async_db_session, company_id=company.id, product=product, quantity=5)

    product.enable_preorder(lead_days=3)
    product.set_extra({"preorder": {"mode": "manual"}})
    await async_db_session.commit()

    result = await evaluate_preorder_state(async_db_session, company_id=company.id, product_id=product.id)
    assert result["changed"] is False

    await async_db_session.refresh(product)
    assert product.is_preorder_enabled is True
    assert product.get_extra().get("preorder", {}).get("mode") == "manual"


async def test_preorder_policy_top_plan_only(async_db_session, factory):
    company = await factory["create_company"]()
    await _set_company_settings(
        async_db_session,
        company.id,
        {"preorders.auto_on_oos": True, "preorders.auto_on_oos_top_plan_only": True},
    )

    await _set_subscription_plan(async_db_session, company_id=company.id, plan="start")

    product = await factory["create_product"](company=company, stock_quantity=0)
    await _seed_stock(async_db_session, company_id=company.id, product=product, quantity=0)

    result = await evaluate_preorder_state(async_db_session, company_id=company.id, product_id=product.id)
    assert result["changed"] is False

    await async_db_session.refresh(product)
    assert product.is_preorder_enabled is False
