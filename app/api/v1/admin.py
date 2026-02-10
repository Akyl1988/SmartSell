from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import sqlalchemy as sa
from fastapi import APIRouter, Body, Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.admin.integrations import router as integrations_router
from app.core.db import get_async_db
from app.core.dependencies import require_platform_admin
from app.core.exceptions import AuthorizationError, NotFoundError, _ensure_request_id
from app.core.logging import audit_logger
from app.core.subscriptions.plan_catalog import get_plan, normalize_plan_id
from app.models.billing import Subscription, WalletBalance, WalletTransaction
from app.models.company import Company
from app.models.kaspi_mc_session import KaspiMcSession
from app.models.kaspi_offer import KaspiOffer
from app.models.kaspi_trial_grant import KaspiTrialGrant
from app.models.subscription_override import SubscriptionOverride
from app.models.user import User
from app.services.campaign_runner import run_due_campaigns
from app.services.subscriptions import activate_plan, renew_if_due

router = APIRouter(
    prefix="/api/v1/admin",
    tags=["admin"],
    dependencies=[Depends(require_platform_admin)],
)
router.include_router(integrations_router)


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
    companyId: int = Field(..., ge=1)
    amount: Decimal = Field(..., gt=0)
    currency: str = Field(..., min_length=3, max_length=8)
    external_reference: str | None = Field(default=None, max_length=128)
    comment: str | None = Field(default=None, max_length=500)


class WalletTopupOut(BaseModel):
    company_id: int
    wallet_id: int
    transaction_id: int
    currency: str
    balance: str
    amount: str


class SubscriptionTrialIn(BaseModel):
    companyId: int = Field(..., ge=1)
    plan: str = Field(default="pro", min_length=2, max_length=32)
    trial_days: int = Field(default=15, ge=1, le=15)


class SubscriptionKaspiTrialIn(BaseModel):
    companyId: int = Field(..., ge=1)
    merchant_uid: str = Field(..., min_length=1, max_length=128)
    plan: str = Field(default="pro", min_length=2, max_length=32)
    trial_days: int = Field(default=15, ge=1, le=15)


@router.post(
    "/tasks/subscriptions/renew/run",
    summary="Run subscription renewal task (platform admin)",
)
async def run_subscription_renew_task(
    request: Request,
    admin: User = Depends(require_platform_admin),
    db: AsyncSession = Depends(get_async_db),
) -> dict:
    _ = admin
    processed = await renew_if_due(db, now=datetime.now(UTC))
    if processed:
        await db.commit()
    else:
        await db.rollback()
    rid = _ensure_request_id(request)
    return {"ok": True, "processed": processed, "request_id": rid}


class CampaignRunIn(BaseModel):
    limit: int | None = Field(default=100, ge=1)
    companyId: int | None = Field(default=None, ge=1, alias="company_id")
    dry_run: bool = False

    model_config = ConfigDict(populate_by_name=True)


@router.post(
    "/tasks/campaigns/run",
    summary="Run campaign processing task (platform admin)",
)
async def run_campaigns_task(
    request: Request,
    payload: CampaignRunIn | None = Body(default=None),
    limit: int = Query(100, ge=1),
    company_id_param: int | None = Query(default=None, ge=1, alias="company_id"),
    dry_run: bool = Query(False),
    admin: User = Depends(require_platform_admin),
    db: AsyncSession = Depends(get_async_db),
) -> dict:
    _ = admin
    resolved_limit = payload.limit if payload and payload.limit is not None else limit
    resolved_company_id = payload.companyId if payload else company_id_param
    resolved_dry_run = payload.dry_run if payload else dry_run

    if resolved_company_id is None:
        raise NotFoundError("company_id_required", code="company_id_required", http_status=400)

    rid = _ensure_request_id(request)
    if resolved_dry_run:
        return {"processed": 0}

    processed = await run_due_campaigns(
        db,
        company_id=resolved_company_id,
        request_id=rid,
        limit=resolved_limit,
        now=datetime.now(UTC),
    )
    return {"processed": processed}


class SubscriptionActivateIn(BaseModel):
    companyId: int = Field(..., ge=1)
    plan: str = Field(..., min_length=2, max_length=32)


class SubscriptionAdminOut(BaseModel):
    id: int
    company_id: int
    plan: str
    status: str
    billing_cycle: str
    price: Decimal
    currency: str
    started_at: datetime | None
    period_start: datetime | None
    period_end: datetime | None
    next_billing_date: datetime | None
    grace_until: datetime | None
    billing_anchor_day: int | None

    model_config = ConfigDict(from_attributes=True)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _ceil_to_midnight_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    midnight = dt.replace(hour=0, minute=0, second=0, microsecond=0)
    if dt > midnight:
        midnight = midnight + timedelta(days=1)
    return midnight


async def _grant_trial_subscription(
    db: AsyncSession,
    *,
    company_id: int,
    plan_code: str,
    trial_days: int,
    now: datetime | None = None,
) -> Subscription:
    plan_id = normalize_plan_id(plan_code, default=None)
    plan = get_plan(plan_id, default=None)
    if not plan_id or plan is None:
        raise AuthorizationError("plan_not_found", code="plan_not_found", http_status=400)

    now = now or _utc_now()
    period_end = now + timedelta(days=trial_days)
    grace_until = _ceil_to_midnight_utc(period_end + timedelta(days=3))

    stmt = select(Subscription).where(Subscription.company_id == company_id).where(Subscription.deleted_at.is_(None))
    sub = (await db.execute(stmt)).scalar_one_or_none()
    if sub is None:
        sub = Subscription(company_id=company_id)
        db.add(sub)

    sub.plan = plan_id
    sub.status = "trialing"
    sub.billing_cycle = "monthly"
    sub.price = Decimal(plan.price)
    sub.currency = plan.currency
    sub.started_at = now
    sub.period_start = now
    sub.period_end = period_end
    sub.next_billing_date = period_end
    sub.billing_anchor_day = now.day
    sub.grace_until = grace_until
    sub.expires_at = period_end

    await db.flush()
    return sub


async def _resolve_company(
    *,
    db: AsyncSession,
    company_id: int | None,
) -> Company:
    if company_id is None:
        raise NotFoundError("company_id_required", code="company_id_required", http_status=400)
    company = await db.get(Company, company_id)
    if not company:
        raise NotFoundError("company_not_found", code="company_not_found", http_status=404)
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


@router.post(
    "/subscriptions/trial",
    response_model=SubscriptionAdminOut,
    summary="Grant trial subscription (platform admin)",
)
async def grant_trial_subscription(
    payload: SubscriptionTrialIn,
    admin: User = Depends(require_platform_admin),
    db: AsyncSession = Depends(get_async_db),
) -> SubscriptionAdminOut:
    _ = admin
    company = await db.get(Company, payload.companyId)
    if not company:
        raise NotFoundError("company_not_found", code="company_not_found", http_status=404)

    sub = await _grant_trial_subscription(
        db,
        company_id=payload.companyId,
        plan_code=payload.plan,
        trial_days=payload.trial_days,
    )
    await db.commit()
    await db.refresh(sub)

    audit_logger.log_system_event(
        level="info",
        event="subscription_trial_granted",
        message="Subscription trial granted",
        meta={
            "company_id": payload.companyId,
            "plan": sub.plan,
            "period_end": sub.period_end.isoformat() if sub.period_end else None,
            "grace_until": sub.grace_until.isoformat() if sub.grace_until else None,
        },
    )

    return SubscriptionAdminOut.model_validate(sub)


@router.post(
    "/subscriptions/activate",
    response_model=SubscriptionAdminOut,
    summary="Activate subscription from wallet (platform admin)",
)
async def activate_subscription_admin(
    payload: SubscriptionActivateIn,
    admin: User = Depends(require_platform_admin),
    db: AsyncSession = Depends(get_async_db),
) -> SubscriptionAdminOut:
    _ = admin
    company = await db.get(Company, payload.companyId)
    if not company:
        raise NotFoundError("company_not_found", code="company_not_found", http_status=404)

    try:
        sub = await activate_plan(db, company_id=payload.companyId, plan_code=payload.plan)
        await db.commit()
        await db.refresh(sub)
    except ValueError as exc:
        msg = str(exc).lower()
        if "insufficient" in msg:
            raise AuthorizationError(
                "insufficient_wallet_balance",
                code="insufficient_wallet_balance",
                http_status=400,
            )
        if "unknown plan" in msg:
            raise AuthorizationError("plan_not_found", code="plan_not_found", http_status=400)
        if "currency" in msg:
            raise AuthorizationError("wallet_currency_mismatch", code="wallet_currency_mismatch", http_status=400)
        raise

    audit_logger.log_system_event(
        level="info",
        event="subscription_activated_admin",
        message="Subscription activated by admin",
        meta={
            "company_id": payload.companyId,
            "plan": sub.plan,
        },
    )

    return SubscriptionAdminOut.model_validate(sub)


@router.post(
    "/subscriptions/trial/kaspi",
    response_model=SubscriptionAdminOut,
    summary="Grant Kaspi trial subscription (platform admin)",
)
async def grant_kaspi_trial_subscription(
    payload: SubscriptionKaspiTrialIn,
    admin: User = Depends(require_platform_admin),
    db: AsyncSession = Depends(get_async_db),
) -> SubscriptionAdminOut:
    _ = admin
    company = await db.get(Company, payload.companyId)
    if not company:
        raise NotFoundError("company_not_found", code="company_not_found", http_status=404)

    merchant_uid = payload.merchant_uid.strip()
    if not merchant_uid:
        raise AuthorizationError("merchant_uid_required", code="merchant_uid_required", http_status=400)

    linked_offer = (
        await db.execute(
            select(KaspiOffer.id)
            .where(KaspiOffer.company_id == payload.companyId)
            .where(KaspiOffer.merchant_uid == merchant_uid)
            .limit(1)
        )
    ).scalar_one_or_none()
    linked_mc = (
        await db.execute(
            select(KaspiMcSession.id)
            .where(KaspiMcSession.company_id == payload.companyId)
            .where(KaspiMcSession.merchant_uid == merchant_uid)
            .limit(1)
        )
    ).scalar_one_or_none()

    if not linked_offer and not linked_mc:
        raise AuthorizationError(
            "merchant_uid_not_linked",
            code="merchant_uid_not_linked",
            http_status=400,
        )

    now = _utc_now()
    trial_ends_at = now + timedelta(days=payload.trial_days)
    grant = KaspiTrialGrant(
        provider="kaspi",
        merchant_uid=merchant_uid,
        company_id=payload.companyId,
        trial_ends_at=trial_ends_at,
        status="active",
        granted_at=now,
    )
    db.add(grant)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise AuthorizationError(
            "trial_already_used_for_merchant_uid",
            code="trial_already_used_for_merchant_uid",
            http_status=409,
            extra={"merchant_uid": merchant_uid},
        )

    sub = await _grant_trial_subscription(
        db,
        company_id=payload.companyId,
        plan_code=payload.plan,
        trial_days=payload.trial_days,
        now=now,
    )
    grant.subscription_id = sub.id

    await db.commit()
    await db.refresh(sub)

    audit_logger.log_system_event(
        level="info",
        event="subscription_trial_granted",
        message="Kaspi subscription trial granted",
        meta={
            "company_id": payload.companyId,
            "plan": sub.plan,
            "merchant_uid": merchant_uid,
            "period_end": sub.period_end.isoformat() if sub.period_end else None,
            "grace_until": sub.grace_until.isoformat() if sub.grace_until else None,
        },
    )

    return SubscriptionAdminOut.model_validate(sub)


@router.get(
    "/subscription-overrides",
    response_model=list[SubscriptionOverrideOut],
    summary="List subscription overrides",
)
async def list_subscription_overrides(
    provider: str = Query("kaspi"),
    companyId: int | None = Query(None),
    current_user: User = Depends(require_platform_admin),
    db: AsyncSession = Depends(get_async_db),
) -> list[SubscriptionOverrideOut]:
    _ = current_user
    company = await _resolve_company(db=db, company_id=companyId)
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
    current_user: User = Depends(require_platform_admin),
    db: AsyncSession = Depends(get_async_db),
) -> SubscriptionOverrideOut:
    company = await _resolve_company(db=db, company_id=payload.company_id)
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
    current_user: User = Depends(require_platform_admin),
    db: AsyncSession = Depends(get_async_db),
) -> dict[str, str]:
    _ = current_user
    company = await _resolve_company(db=db, company_id=companyId)
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
