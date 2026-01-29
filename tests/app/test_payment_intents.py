from __future__ import annotations

from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User


async def _get_user(async_db_session: AsyncSession, phone: str) -> User:
    res = await async_db_session.execute(select(User).where(User.phone == phone))
    user = res.scalars().first()
    assert user is not None
    return user


@pytest.mark.asyncio
async def test_payment_intent_fallback_noop(
    async_client: AsyncClient, async_db_session: AsyncSession, company_a_admin_headers
):
    user = await _get_user(async_db_session, "+70000010001")

    resp = await async_client.post(
        "/api/v1/payments/intents",
        headers=company_a_admin_headers,
        json={
            "amount": "10.00",
            "currency": "KZT",
            "customer_id": str(user.id),
            "metadata": {"source": "test"},
        },
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()

    assert data["provider"].startswith("noop")
    assert data["status"] == "created"
    assert data["currency"] == "KZT"
    assert Decimal(str(data["amount"])) == Decimal("10.00")
    assert data["customer_id"] == str(user.id)
    assert data["provider_intent_id"]
    assert data["id"]
    assert data["created_at"]


@pytest.mark.asyncio
async def test_payment_intent_get_tenant_isolation(
    async_client: AsyncClient,
    async_db_session: AsyncSession,
    company_a_admin_headers,
    company_b_admin_headers,
):
    user = await _get_user(async_db_session, "+70000010001")

    created = await async_client.post(
        "/api/v1/payments/intents",
        headers=company_a_admin_headers,
        json={
            "amount": "5.00",
            "currency": "KZT",
            "customer_id": str(user.id),
            "metadata": {"source": "tenant"},
        },
    )
    assert created.status_code == 201, created.text
    intent_id = created.json()["id"]

    foreign = await async_client.get(
        f"/api/v1/payments/intents/{intent_id}",
        headers=company_b_admin_headers,
    )
    assert foreign.status_code == 404

    own = await async_client.get(
        f"/api/v1/payments/intents/{intent_id}",
        headers=company_a_admin_headers,
    )
    assert own.status_code == 200, own.text
    data = own.json()
    assert data["id"] == intent_id
    assert data["customer_id"] == str(user.id)
