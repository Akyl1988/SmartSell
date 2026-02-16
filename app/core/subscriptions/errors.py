from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import desc, nullslast, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import resolve_tenant_company_id
from app.models.billing import Subscription, WalletBalance

_SUB_STATUS_RELEVANT = {"active", "trial", "trialing", "past_due", "frozen", "overdue", "paused"}


def _iso_or_none(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _decimal_or_none(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return str(value)


async def _build_payload(db: AsyncSession, company_id: int | None) -> dict:
    subscription_data = {
        "status": None,
        "plan": None,
        "period_end": None,
        "grace_until": None,
        "next_billing_date": None,
    }
    wallet_data = {"balance": None, "currency": None}

    if company_id is not None:
        stmt = (
            select(Subscription)
            .where(Subscription.company_id == company_id)
            .where(Subscription.deleted_at.is_(None))
            .where(Subscription.status.in_(list(_SUB_STATUS_RELEVANT)))
            .order_by(nullslast(desc(Subscription.period_end)))
            .order_by(desc(Subscription.started_at))
            .order_by(desc(Subscription.created_at))
            .limit(1)
        )
        sub = (await db.execute(stmt)).scalar_one_or_none()
        if sub is not None:
            subscription_data = {
                "status": sub.status,
                "plan": sub.plan,
                "period_end": _iso_or_none(sub.period_end),
                "grace_until": _iso_or_none(getattr(sub, "grace_until", None)),
                "next_billing_date": _iso_or_none(sub.next_billing_date),
            }

        wallet = (
            await db.execute(select(WalletBalance).where(WalletBalance.company_id == company_id).limit(1))
        ).scalar_one_or_none()
        if wallet is not None:
            wallet_data = {
                "balance": _decimal_or_none(wallet.balance),
                "currency": wallet.currency,
            }

    return {
        "code": "SUBSCRIPTION_REQUIRED",
        "detail": "Subscription required",
        "company_id": company_id,
        "subscription": subscription_data,
        "wallet": wallet_data,
        "actions": [
            {"type": "TOPUP_WALLET", "hint": "Пополните кошелек"},
            {"type": "ACTIVATE_PLAN", "hint": "Активируйте тариф после пополнения"},
        ],
    }


async def build_subscription_required_payload(db: AsyncSession, user: object | None) -> dict:
    company_id: int | None = None
    try:
        if user is not None:
            company_id = resolve_tenant_company_id(user, not_found_detail="Company not set")
    except Exception:
        company_id = None
    return await _build_payload(db, company_id)


async def build_subscription_required_payload_for_company(db: AsyncSession, company_id: int | None) -> dict:
    return await _build_payload(db, company_id)


def build_limit_exceeded_payload(*, feature: str, limit: int, used: int) -> dict:
    return {
        "code": "LIMIT_EXCEEDED",
        "detail": "Feature limit exceeded",
        "feature": feature,
        "limit": limit,
        "used": used,
    }


__all__ = [
    "build_subscription_required_payload",
    "build_subscription_required_payload_for_company",
    "build_limit_exceeded_payload",
]
