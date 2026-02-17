from __future__ import annotations

import uuid

import pytest

from app.models.user import User

pytestmark = pytest.mark.asyncio


def _get_user_by_phone(db_session, phone: str) -> User:
    return db_session.query(User).filter(User.phone == phone).one()


async def test_pricing_rules_crud_company_scoped(
    async_client,
    db_session,
    company_a_admin_headers,
    company_b_admin_headers,
):
    user_a = _get_user_by_phone(db_session, "+70000010001")

    payload = {
        "name": f"rule-{uuid.uuid4().hex[:6]}",
        "enabled": True,
        "is_active": True,
        "min_price": "10.00",
        "max_price": "200.00",
        "step": "5.00",
        "undercut": "5.00",
        "cooldown_seconds": 0,
        "max_delta_percent": "20.00",
    }

    created = await async_client.post("/api/v1/pricing/rules", json=payload, headers=company_a_admin_headers)
    assert created.status_code == 201, created.text
    created_payload = created.json()
    rule_id = created_payload.get("id")
    assert created_payload.get("company_id") == user_a.company_id

    listed = await async_client.get("/api/v1/pricing/rules", headers=company_a_admin_headers)
    assert listed.status_code == 200, listed.text
    list_payload = listed.json()
    items = list_payload.get("items") or []
    assert any(item.get("id") == rule_id for item in items)

    fetched = await async_client.get(f"/api/v1/pricing/rules/{rule_id}", headers=company_a_admin_headers)
    assert fetched.status_code == 200, fetched.text
    assert fetched.json().get("id") == rule_id

    forbidden = await async_client.get(f"/api/v1/pricing/rules/{rule_id}", headers=company_b_admin_headers)
    assert forbidden.status_code == 404, forbidden.text

    updated = await async_client.patch(
        f"/api/v1/pricing/rules/{rule_id}",
        json={"name": "rule-updated", "enabled": False},
        headers=company_a_admin_headers,
    )
    assert updated.status_code == 200, updated.text
    assert updated.json().get("name") == "rule-updated"
    assert updated.json().get("enabled") is False

    deleted = await async_client.delete(f"/api/v1/pricing/rules/{rule_id}", headers=company_a_admin_headers)
    assert deleted.status_code == 200, deleted.text
    assert deleted.json().get("message")
