from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio


def _payload():
    return {
        "currency": "KZT",
        "customer_name": "Alice",
        "customer_phone": "+77000000000",
        "notes": "call before delivery",
        "items": [
            {
                "sku": "SKU-001",
                "name": "Test Item",
                "qty": 2,
                "price": "100.00",
            }
        ],
    }


async def test_preorders_employee_forbidden(
    async_client,
    company_a_employee_headers,
):
    created = await async_client.post(
        "/api/v1/preorders",
        json=_payload(),
        headers=company_a_employee_headers,
    )
    assert created.status_code == 403, created.text

    listed = await async_client.get("/api/v1/preorders", headers=company_a_employee_headers)
    assert listed.status_code == 403, listed.text

    forbidden = await async_client.get("/api/v1/preorders/1", headers=company_a_employee_headers)
    assert forbidden.status_code == 403, forbidden.text

    confirm = await async_client.post("/api/v1/preorders/1/confirm", headers=company_a_employee_headers)
    assert confirm.status_code == 403, confirm.text


async def test_preorders_store_admin_flow_and_tenant_isolation(
    async_client,
    company_a_admin_headers,
    company_b_admin_headers,
):
    created = await async_client.post(
        "/api/v1/preorders",
        json=_payload(),
        headers=company_a_admin_headers,
    )
    assert created.status_code == 201, created.text
    preorder_id = created.json().get("id")
    assert preorder_id

    listed = await async_client.get("/api/v1/preorders", headers=company_a_admin_headers)
    assert listed.status_code == 200, listed.text
    items = listed.json().get("items") or []
    assert any(item.get("id") == preorder_id for item in items)

    fetched = await async_client.get(f"/api/v1/preorders/{preorder_id}", headers=company_a_admin_headers)
    assert fetched.status_code == 200, fetched.text
    assert isinstance(fetched.json().get("items"), list)

    forbidden = await async_client.get(f"/api/v1/preorders/{preorder_id}", headers=company_b_admin_headers)
    assert forbidden.status_code == 404, forbidden.text

    updated = await async_client.patch(
        f"/api/v1/preorders/{preorder_id}",
        json={"notes": "updated"},
        headers=company_a_admin_headers,
    )
    assert updated.status_code == 200, updated.text
    assert updated.json().get("notes") == "updated"

    confirmed = await async_client.post(
        f"/api/v1/preorders/{preorder_id}/confirm",
        headers=company_a_admin_headers,
    )
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json().get("status") == "confirmed"

    fulfilled = await async_client.post(
        f"/api/v1/preorders/{preorder_id}/fulfill",
        headers=company_a_admin_headers,
    )
    assert fulfilled.status_code == 200, fulfilled.text
    assert fulfilled.json().get("status") == "fulfilled"


async def test_preorders_transitions_invalid(
    async_client,
    company_a_admin_headers,
):
    created = await async_client.post(
        "/api/v1/preorders",
        json=_payload(),
        headers=company_a_admin_headers,
    )
    assert created.status_code == 201, created.text
    preorder_id = created.json().get("id")
    assert preorder_id

    fulfilled = await async_client.post(
        f"/api/v1/preorders/{preorder_id}/fulfill",
        headers=company_a_admin_headers,
    )
    assert fulfilled.status_code == 409, fulfilled.text

    confirmed = await async_client.post(
        f"/api/v1/preorders/{preorder_id}/confirm",
        headers=company_a_admin_headers,
    )
    assert confirmed.status_code == 200, confirmed.text

    updated = await async_client.patch(
        f"/api/v1/preorders/{preorder_id}",
        json={"notes": "should fail"},
        headers=company_a_admin_headers,
    )
    assert updated.status_code == 409, updated.text

    cancelled = await async_client.post(
        f"/api/v1/preorders/{preorder_id}/cancel",
        headers=company_a_admin_headers,
    )
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json().get("status") == "cancelled"

    cancelled_again = await async_client.post(
        f"/api/v1/preorders/{preorder_id}/cancel",
        headers=company_a_admin_headers,
    )
    assert cancelled_again.status_code == 200, cancelled_again.text
    assert cancelled_again.json().get("status") == "cancelled"
