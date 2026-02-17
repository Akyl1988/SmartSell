from __future__ import annotations

import pytest

from app.models.company import Company
from app.models.product import Product
from app.models.repricing import RepricingDiff, RepricingRule, RepricingRun

pytestmark = pytest.mark.asyncio


async def test_repricing_models_company_scoped(async_db_session):
    company_a = Company(name="Company A")
    company_b = Company(name="Company B")
    async_db_session.add_all([company_a, company_b])
    await async_db_session.flush()

    product_a = Product(company_id=company_a.id, name="P1", slug="p1", sku="SKU1", price=10)
    product_b = Product(company_id=company_b.id, name="P2", slug="p2", sku="SKU2", price=20)
    async_db_session.add_all([product_a, product_b])
    await async_db_session.flush()

    rule_a = RepricingRule(company_id=company_a.id, name="rule-a", enabled=True)
    rule_b = RepricingRule(company_id=company_b.id, name="rule-b", enabled=True)
    async_db_session.add_all([rule_a, rule_b])
    await async_db_session.flush()

    run_a = RepricingRun(company_id=company_a.id, rule_id=rule_a.id, status="completed")
    async_db_session.add(run_a)
    await async_db_session.flush()

    diff_a = RepricingDiff(
        company_id=company_a.id,
        rule_id=rule_a.id,
        run_id=run_a.id,
        product_id=product_a.id,
        sku=product_a.sku,
        old_price=10,
        new_price=9,
        reason="test",
    )
    async_db_session.add(diff_a)
    await async_db_session.commit()

    res_rules = await async_db_session.execute(
        RepricingRule.__table__.select().where(RepricingRule.company_id == company_a.id)
    )
    rules = res_rules.fetchall()
    assert len(rules) == 1
    assert rules[0].name == "rule-a"

    res_diffs = await async_db_session.execute(
        RepricingDiff.__table__.select().where(RepricingDiff.company_id == company_a.id)
    )
    diffs = res_diffs.fetchall()
    assert len(diffs) == 1
    assert diffs[0].product_id == product_a.id
