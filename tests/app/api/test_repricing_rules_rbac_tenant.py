from __future__ import annotations

import uuid

import pytest

from app.models.user import User

pytestmark = pytest.mark.asyncio


def _get_user_by_phone(db_session, phone: str) -> User:
    return db_session.query(User).filter(User.phone == phone).one()


def _rule_payload() -> dict:
    return {
        "name": f"rule-{uuid.uuid4().hex[:6]}",
        "enabled": True,
        "is_active": True,
        "scope_type": "all",
        "step": "5.00",
        "rounding_mode": "nearest",
    }


async def test_repricing_rules_tenant_isolation(
    async_client,
    db_session,
    company_a_admin_headers,
    company_b_admin_headers,
):
    user_a = _get_user_by_phone(db_session, "+70000010001")

    created = await async_client.post(
        "/api/v1/repricing/rules",
        json=_rule_payload(),
        headers=company_a_admin_headers,
    )
    assert created.status_code == 201, created.text
    rule_id = created.json().get("id")
    assert rule_id
    assert created.json().get("company_id") == user_a.company_id

    listed = await async_client.get("/api/v1/repricing/rules", headers=company_a_admin_headers)
    assert listed.status_code == 200, listed.text
    items = listed.json().get("items") or []
    assert any(item.get("id") == rule_id for item in items)

    forbidden_get = await async_client.get(
        f"/api/v1/repricing/rules/{rule_id}",
        headers=company_b_admin_headers,
    )
    assert forbidden_get.status_code == 404, forbidden_get.text

    forbidden_patch = await async_client.patch(
        f"/api/v1/repricing/rules/{rule_id}",
        json={"name": "rule-updated"},
        headers=company_b_admin_headers,
    )
    assert forbidden_patch.status_code == 404, forbidden_patch.text

    forbidden_delete = await async_client.delete(
        f"/api/v1/repricing/rules/{rule_id}",
        headers=company_b_admin_headers,
    )
    assert forbidden_delete.status_code == 404, forbidden_delete.text


async def test_repricing_platform_admin_forbidden(async_client, auth_headers):
    resp = await async_client.get("/api/v1/repricing/rules", headers=auth_headers)
    assert resp.status_code == 403, resp.text


async def test_repricing_run_triggered_by_store_admin(
    async_client,
    company_a_admin_headers,
):
    created = await async_client.post(
        "/api/v1/repricing/rules",
        json=_rule_payload(),
        headers=company_a_admin_headers,
    )
    assert created.status_code == 201, created.text

    run_resp = await async_client.post("/api/v1/repricing/run", headers=company_a_admin_headers)
    assert run_resp.status_code == 200, run_resp.text
    assert run_resp.json().get("run_id")


async def test_repricing_run_details_include_items_and_tenant_isolation(
    async_client,
    company_a_admin_headers,
    company_b_admin_headers,
):
    created = await async_client.post(
        "/api/v1/repricing/rules",
        json=_rule_payload(),
        headers=company_a_admin_headers,
    )
    assert created.status_code == 201, created.text

    run_resp = await async_client.post("/api/v1/repricing/run", headers=company_a_admin_headers)
    assert run_resp.status_code == 200, run_resp.text
    run_id = run_resp.json().get("run_id")
    assert run_id

    details = await async_client.get(f"/api/v1/repricing/runs/{run_id}", headers=company_a_admin_headers)
    assert details.status_code == 200, details.text
    payload = details.json()
    assert "items" in payload
    assert isinstance(payload.get("items"), list)

    forbidden = await async_client.get(f"/api/v1/repricing/runs/{run_id}", headers=company_b_admin_headers)
    assert forbidden.status_code == 404, forbidden.text
