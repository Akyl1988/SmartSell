from __future__ import annotations

import pytest

from app.models.company import Company
from app.models.kaspi_offer import KaspiOffer

pytestmark = pytest.mark.asyncio


async def test_kaspi_trial_granted_once_per_merchant_uid(async_client, async_db_session, auth_headers):
    company_a = Company(id=9101, name="Kaspi Trial A")
    async_db_session.add(company_a)
    await async_db_session.commit()

    async_db_session.add(
        KaspiOffer(
            company_id=company_a.id,
            merchant_uid="M-TRIAL-1",
            sku="SKU-A",
            title="Item A",
            price=1000,
        )
    )
    await async_db_session.commit()

    first = await async_client.post(
        "/api/v1/admin/subscriptions/trial/kaspi",
        headers=auth_headers,
        json={"companyId": company_a.id, "merchant_uid": "M-TRIAL-1", "plan": "pro", "trial_days": 15},
    )
    assert first.status_code == 200, first.text

    company_b = Company(id=9102, name="Kaspi Trial B")
    async_db_session.add(company_b)
    await async_db_session.commit()

    async_db_session.add(
        KaspiOffer(
            company_id=company_b.id,
            merchant_uid="M-TRIAL-1",
            sku="SKU-B",
            title="Item B",
            price=1000,
        )
    )
    await async_db_session.commit()

    second = await async_client.post(
        "/api/v1/admin/subscriptions/trial/kaspi",
        headers=auth_headers,
        json={"companyId": company_b.id, "merchant_uid": "M-TRIAL-1", "plan": "pro", "trial_days": 15},
    )
    assert second.status_code == 409, second.text
    payload = second.json()
    assert payload.get("detail") == "trial_already_used_for_merchant_uid"


async def test_trial_requires_merchant_uid_linked_to_company(async_client, async_db_session, auth_headers):
    company = Company(id=9103, name="Kaspi Trial C")
    async_db_session.add(company)
    await async_db_session.commit()

    resp = await async_client.post(
        "/api/v1/admin/subscriptions/trial/kaspi",
        headers=auth_headers,
        json={"companyId": company.id, "merchant_uid": "M-NOT-LINKED", "plan": "pro", "trial_days": 15},
    )
    assert resp.status_code == 400, resp.text
    payload = resp.json()
    assert payload.get("detail") == "merchant_uid_not_linked"
