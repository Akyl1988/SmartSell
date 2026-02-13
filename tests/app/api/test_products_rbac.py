from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.asyncio


def _product_payload(label: str) -> dict[str, object]:
    slug = f"rbac-{uuid.uuid4().hex[:8]}"
    sku = f"SKU-{uuid.uuid4().hex[:8].upper()}"
    return {
        "name": f"RBAC {label}",
        "slug": slug,
        "sku": sku,
        "price": 10.0,
    }


async def _create_product(async_client, headers, label: str):
    payload = _product_payload(label)
    return await async_client.post("/api/v1/products", json=payload, headers=headers)


async def test_store_admin_can_create_and_get(async_client, company_a_admin_headers):
    created = await _create_product(async_client, company_a_admin_headers, "Store Admin")
    assert created.status_code == 200, created.text
    product_id = created.json().get("id")
    assert product_id

    fetched = await async_client.get(f"/api/v1/products/{product_id}", headers=company_a_admin_headers)
    assert fetched.status_code == 200, fetched.text


async def test_employee_forbidden_on_create(async_client, company_a_employee_headers):
    created = await _create_product(async_client, company_a_employee_headers, "Employee")
    assert created.status_code == 403, created.text
    payload = created.json()
    assert payload.get("code") == "ADMIN_REQUIRED"
    assert payload.get("request_id")


async def test_platform_admin_can_access_other_company(async_client, company_b_admin_headers, auth_headers):
    created = await _create_product(async_client, company_b_admin_headers, "Other Company")
    assert created.status_code == 200, created.text
    product_id = created.json().get("id")
    assert product_id

    fetched = await async_client.get(f"/api/v1/products/{product_id}", headers=auth_headers)
    assert fetched.status_code == 200, fetched.text
