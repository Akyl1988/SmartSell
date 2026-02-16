from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

import pytest

pytestmark = pytest.mark.asyncio


def _product_payload() -> dict[str, object]:
    suffix = uuid.uuid4().hex[:8]
    return {
        "name": f"Preorder Enabled {suffix}",
        "slug": f"preorder-enabled-{suffix}",
        "sku": f"PRE-{suffix.upper()}",
        "price": "100.00",
        "stock_quantity": 0,
        "is_active": True,
    }


async def test_update_product_enables_preorder_and_allows_preorder_creation(
    async_client,
    company_a_admin_headers,
):
    created = await async_client.post(
        "/api/v1/products",
        headers=company_a_admin_headers,
        json=_product_payload(),
    )
    assert created.status_code == 200, created.text
    product_id = created.json().get("id")
    assert product_id

    before_ts = int(datetime.utcnow().timestamp())
    update_payload = {
        "is_preorder_enabled": True,
        "preorder_lead_days": 3,
        "preorder_deposit": "10.00",
        "preorder_note": "ships soon",
        "preorder_show_zero_stock": False,
    }
    updated = await async_client.put(
        f"/api/v1/products/{product_id}",
        headers=company_a_admin_headers,
        json=update_payload,
    )
    assert updated.status_code == 200, updated.text
    updated_payload = updated.json()
    assert updated_payload.get("is_preorder_enabled") is True
    assert updated_payload.get("preorder_lead_days") == 3
    assert Decimal(str(updated_payload.get("preorder_deposit"))) == Decimal("10.00")
    assert updated_payload.get("preorder_note") == "ships soon"
    assert updated_payload.get("preorder_show_zero_stock") is False
    preorder_until = updated_payload.get("preorder_until")
    assert isinstance(preorder_until, int)
    assert preorder_until >= before_ts
    assert preorder_until <= before_ts + (3 * 86400) + 10

    fetched = await async_client.get(
        f"/api/v1/products/{product_id}",
        headers=company_a_admin_headers,
    )
    assert fetched.status_code == 200, fetched.text
    fetched_payload = fetched.json()
    assert fetched_payload.get("is_preorder_enabled") is True
    assert fetched_payload.get("preorder_until") == preorder_until

    preorder_created = await async_client.post(
        "/api/v1/preorders",
        headers=company_a_admin_headers,
        json={
            "product_id": product_id,
            "qty": 1,
            "customer_name": "Alice",
        },
    )
    assert preorder_created.status_code == 201, preorder_created.text
