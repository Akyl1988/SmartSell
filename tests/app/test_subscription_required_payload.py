from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.core.security import get_password_hash
from app.core.subscriptions.errors import build_subscription_required_payload
from app.models.billing import Subscription, WalletBalance
from app.models.company import Company
from app.models.user import User

pytestmark = pytest.mark.asyncio


async def test_build_subscription_required_payload_with_wallet_and_grace(async_db_session):
    company = Company(id=9201, name="Payload Co")
    async_db_session.add(company)
    await async_db_session.flush()

    user = User(
        company_id=company.id,
        phone="77000092001",
        hashed_password=get_password_hash("Secret123!"),
        role="admin",
        is_active=True,
        is_verified=True,
    )
    async_db_session.add(user)

    now = datetime.now(UTC)
    period_end = now + timedelta(days=1)
    grace_until = period_end + timedelta(days=3)
    sub = Subscription(
        company_id=company.id,
        plan="business",
        status="past_due",
        billing_cycle="monthly",
        price=Decimal("30.00"),
        currency="KZT",
        started_at=now,
        period_start=now,
        period_end=period_end,
        next_billing_date=period_end,
        grace_until=grace_until,
    )
    async_db_session.add(sub)

    wallet = WalletBalance(company_id=company.id, balance=Decimal("123.45"), currency="KZT")
    async_db_session.add(wallet)

    await async_db_session.commit()

    payload = await build_subscription_required_payload(async_db_session, user)
    assert payload["code"] == "SUBSCRIPTION_REQUIRED"
    assert payload["company_id"] == company.id
    assert payload["subscription"]["status"] == "past_due"
    assert payload["subscription"]["plan"] == "business"
    assert payload["subscription"]["period_end"] == period_end.isoformat()
    assert payload["subscription"]["grace_until"] == grace_until.isoformat()
    assert payload["wallet"]["balance"] == "123.45"
    assert payload["wallet"]["currency"] == "KZT"
