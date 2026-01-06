import pytest

pytestmark = pytest.mark.asyncio


async def _get_items(resp_json):
    if isinstance(resp_json, dict) and "items" in resp_json:
        return resp_json["items"]
    return resp_json if isinstance(resp_json, list) else []


async def _create_product(client, headers, name, sku):
    resp = await client.post(
        "/api/v1/products",
        json={
            "name": name,
            "slug": f"{sku.lower()}-slug",
            "sku": sku,
            "price": 1,
            "stock_quantity": 0,
        },
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["id"]


async def _create_campaign(client, headers, title):
    resp = await client.post(
        "/api/v1/campaigns/",
        json={
            "title": title,
            "messages": [],
            "active": True,
        },
        headers=headers,
    )
    assert resp.status_code in (200, 201), resp.text
    return resp.json()["id"]


async def test_products_cross_tenant_isolation(client, company_a_admin_headers, company_b_admin_headers):
    prod_b = await _create_product(client, company_b_admin_headers, "b-prod-1", "B-SKU-1")

    r = await client.get("/api/v1/products", headers=company_a_admin_headers)
    assert r.status_code == 200, r.text
    items = await _get_items(r.json())
    assert all(p.get("id") != prod_b for p in items)

    r = await client.get(f"/api/v1/products/{prod_b}", headers=company_a_admin_headers)
    assert r.status_code == 404

    r = await client.put(
        f"/api/v1/products/{prod_b}",
        json={"name": "should-not-update"},
        headers=company_a_admin_headers,
    )
    assert r.status_code == 404

    r = await client.delete(f"/api/v1/products/{prod_b}", headers=company_a_admin_headers)
    assert r.status_code == 404


async def test_products_rbac_roles(
    client, company_a_admin_headers, company_a_manager_headers, company_a_analyst_headers, company_a_storekeeper_headers
):
    prod_a = await _create_product(client, company_a_admin_headers, "a-prod-1", "A-SKU-1")

    r = await client.get(f"/api/v1/products/{prod_a}", headers=company_a_analyst_headers)
    assert r.status_code == 200, r.text

    r = await client.put(
        f"/api/v1/products/{prod_a}",
        json={"name": "denied"},
        headers=company_a_analyst_headers,
    )
    assert r.status_code == 403

    r = await client.delete(f"/api/v1/products/{prod_a}", headers=company_a_storekeeper_headers)
    assert r.status_code == 403

    r = await client.put(
        f"/api/v1/products/{prod_a}",
        json={"name": "allowed"},
        headers=company_a_manager_headers,
    )
    assert r.status_code == 200, r.text


async def test_campaigns_cross_tenant_isolation(client, company_a_admin_headers, company_b_admin_headers):
    camp_b = await _create_campaign(client, company_b_admin_headers, "b-camp-1")

    r = await client.get("/api/v1/campaigns/", headers=company_a_admin_headers)
    assert r.status_code == 200, r.text
    items = await _get_items(r.json())
    assert all(c.get("id") != camp_b for c in items)

    r = await client.get(f"/api/v1/campaigns/{camp_b}", headers=company_a_admin_headers)
    assert r.status_code == 404
