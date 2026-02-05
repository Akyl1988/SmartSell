from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_async_db
from app.core.dependencies import require_platform_admin
from app.core.exceptions import AuthorizationError, NotFoundError
from app.core.logging import audit_logger
from app.core.security import get_current_user, resolve_tenant_company_id
from app.models.billing import WalletBalance, WalletTransaction
from app.models.company import Company
from app.models.subscription_override import SubscriptionOverride
from app.models.user import User

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])


class SubscriptionOverrideIn(BaseModel):
    active_until: datetime | None = Field(default=None)
    note: str | None = Field(default=None, max_length=2000)
    company_id: int | None = None


class SubscriptionOverrideOut(BaseModel):
    id: int
    provider: str
    company_id: int
    merchant_uid: str
    active_until: datetime | None = None
    note: str | None = None
    created_by_user_id: int | None = None
    created_at: datetime
    revoked_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


class WalletTopupIn(BaseModel):
    companyId: int
    amount: Decimal = Field(..., gt=0)
    currency: str = Field(default="KZT", min_length=3, max_length=8)
    external_reference: str | None = Field(default=None, max_length=128)
    comment: str | None = Field(default=None, max_length=255)


class WalletTopupOut(BaseModel):
    company_id: int
    wallet_id: int
    transaction_id: int
    currency: str
    balance: str
    amount: str


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _require_owner_or_superuser(*, current_user: User, company: Company | None) -> None:
    if not company:
        raise NotFoundError("company_not_found", code="company_not_found", http_status=404)
    if getattr(current_user, "is_superuser", False):
        return
    if company.owner_id != current_user.id:
        raise AuthorizationError("forbidden", code="forbidden", http_status=403)


async def _resolve_company(
    *,
    db: AsyncSession,
    current_user: User,
    company_id: int | None,
) -> Company:
    resolved_id = company_id or resolve_tenant_company_id(current_user, not_found_detail="Company not set")
    company = await db.get(Company, resolved_id)
    _require_owner_or_superuser(current_user=current_user, company=company)
    return company


@router.post(
    "/wallet/topup",
    response_model=WalletTopupOut,
    summary="Manual company wallet top-up (platform admin)",
)
async def manual_wallet_topup(
    payload: WalletTopupIn,
    admin: User = Depends(require_platform_admin),
    db: AsyncSession = Depends(get_async_db),
) -> WalletTopupOut:
    _ = admin
    company = await db.get(Company, payload.companyId)
    if not company:
        raise NotFoundError("company_not_found", code="company_not_found", http_status=404)

    wallet = await WalletBalance.get_for_company_async(
        db,
        payload.companyId,
        create_if_missing=True,
        currency=payload.currency,
    )
    if (wallet.currency or "").upper() != payload.currency.upper():
        raise AuthorizationError("wallet_currency_mismatch", code="wallet_currency_mismatch", http_status=400)

    amount = Decimal(str(payload.amount))

    if payload.external_reference:
        existing_stmt = select(WalletTransaction).where(
            WalletTransaction.wallet_id == wallet.id,
            WalletTransaction.client_request_id == payload.external_reference,
        )
        existing = (await db.execute(existing_stmt)).scalar_one_or_none()
        if existing:
            return WalletTopupOut(
                company_id=payload.companyId,
                wallet_id=wallet.id,
                transaction_id=existing.id,
                currency=wallet.currency,
                balance=str(existing.balance_after),
                amount=str(existing.amount),
            )

    before = wallet.balance or Decimal("0")
    after = before + amount
    wallet.balance = after
    trx = WalletTransaction(
        wallet_id=wallet.id,
        transaction_type="manual_topup",
        amount=amount,
        balance_before=before,
        balance_after=after,
        description=payload.comment or "manual_topup",
        reference_type="manual_topup",
        client_request_id=payload.external_reference,
        extra_data=json.dumps(
            {
                "external_reference": payload.external_reference,
                "comment": payload.comment,
            },
            ensure_ascii=False,
        ),
    )
    db.add(trx)
    try:
        await db.flush()
        await db.commit()
    except IntegrityError:
        await db.rollback()
        if payload.external_reference:
            existing_stmt = select(WalletTransaction).where(
                WalletTransaction.wallet_id == wallet.id,
                WalletTransaction.client_request_id == payload.external_reference,
            )
            existing = (await db.execute(existing_stmt)).scalar_one_or_none()
            if existing:
                return WalletTopupOut(
                    company_id=payload.companyId,
                    wallet_id=wallet.id,
                    transaction_id=existing.id,
                    currency=wallet.currency,
                    balance=str(existing.balance_after),
                    amount=str(existing.amount),
                )
        raise

    audit_logger.log_system_event(
        level="info",
        event="wallet_manual_topup",
        message="Wallet credited manually",
        meta={
            "company_id": payload.companyId,
            "wallet_id": wallet.id,
            "amount": str(amount),
            "currency": payload.currency,
            "transaction_id": trx.id,
        },
    )

    return WalletTopupOut(
        company_id=payload.companyId,
        wallet_id=wallet.id,
        transaction_id=trx.id,
        currency=wallet.currency,
        balance=str(wallet.balance),
        amount=str(amount),
    )


@router.get(
    "/subscription-overrides",
    response_model=list[SubscriptionOverrideOut],
    summary="List subscription overrides",
)
async def list_subscription_overrides(
    provider: str = Query("kaspi"),
    companyId: int | None = Query(None),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
) -> list[SubscriptionOverrideOut]:
    company = await _resolve_company(db=db, current_user=current_user, company_id=companyId)
    stmt = sa.select(SubscriptionOverride).where(
        SubscriptionOverride.company_id == company.id,
        SubscriptionOverride.provider == provider,
    )
    rows = (await db.execute(stmt.order_by(SubscriptionOverride.created_at.desc()))).scalars().all()
    return [SubscriptionOverrideOut.model_validate(row) for row in rows]


@router.put(
    "/subscription-overrides/kaspi/{merchant_uid}",
    response_model=SubscriptionOverrideOut,
    summary="Upsert subscription override (Kaspi)",
)
async def upsert_subscription_override_kaspi(
    merchant_uid: str,
    payload: SubscriptionOverrideIn,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
) -> SubscriptionOverrideOut:
    company = await _resolve_company(db=db, current_user=current_user, company_id=payload.company_id)
    merchant = merchant_uid.strip()
    stmt = sa.select(SubscriptionOverride).where(
        SubscriptionOverride.company_id == company.id,
        SubscriptionOverride.provider == "kaspi",
        SubscriptionOverride.merchant_uid == merchant,
    )
    row = (await db.execute(stmt)).scalars().first()
    if row:
        row.active_until = payload.active_until
        row.note = payload.note
        row.revoked_at = None
    else:
        row = SubscriptionOverride(
            provider="kaspi",
            company_id=company.id,
            merchant_uid=merchant,
            active_until=payload.active_until,
            note=payload.note,
            created_by_user_id=current_user.id,
        )
        db.add(row)
    await db.commit()
    await db.refresh(row)
    return SubscriptionOverrideOut.model_validate(row)


@router.delete(
    "/subscription-overrides/kaspi/{merchant_uid}",
    summary="Revoke subscription override (Kaspi)",
)
async def revoke_subscription_override_kaspi(
    merchant_uid: str,
    companyId: int | None = Query(None),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
) -> dict[str, str]:
    company = await _resolve_company(db=db, current_user=current_user, company_id=companyId)
    stmt = sa.select(SubscriptionOverride).where(
        SubscriptionOverride.company_id == company.id,
        SubscriptionOverride.provider == "kaspi",
        SubscriptionOverride.merchant_uid == merchant_uid,
    )
    row = (await db.execute(stmt)).scalars().first()
    if not row:
        raise NotFoundError("override_not_found", code="override_not_found", http_status=404)
    row.revoked_at = _utc_now()
    await db.commit()
    return {"status": "revoked", "merchant_uid": merchant_uid}
