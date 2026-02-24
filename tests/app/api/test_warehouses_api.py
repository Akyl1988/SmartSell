from __future__ import annotations

from uuid import uuid4

import pytest


@pytest.mark.asyncio
async def test_create_warehouse_returns_id(async_client, company_a_admin_headers):
    name = f"Warehouse {uuid4().hex[:8]}"
    payload = {"name": name, "is_main": False}

    resp = await async_client.post("/api/v1/warehouses", json=payload, headers=company_a_admin_headers)
    assert resp.status_code == 201, resp.text

    data = resp.json()
    assert isinstance(data.get("id"), int)
    assert data.get("name") == name


@pytest.mark.asyncio
async def test_list_warehouses_includes_id(async_client, company_a_admin_headers):
    name = f"Warehouse {uuid4().hex[:8]}"
    payload = {"name": name, "is_main": False}

    create_resp = await async_client.post("/api/v1/warehouses", json=payload, headers=company_a_admin_headers)
    assert create_resp.status_code == 201, create_resp.text

    resp = await async_client.get("/api/v1/warehouses?page=1&per_page=50", headers=company_a_admin_headers)
    assert resp.status_code == 200, resp.text

    body = resp.json()
    items = body.get("items") or []
    matched = next((item for item in items if item.get("name") == name), None)
    assert matched is not None, "Expected warehouse to appear in list"
    assert isinstance(matched.get("id"), int)
