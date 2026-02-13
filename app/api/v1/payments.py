# app/api/v1/payments.py
from __future__ import annotations

import hashlib
import hmac
import logging
import time
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Path, Query, Request, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.db import get_async_db
from app.core.dependencies import (
    get_current_verified_user,
    require_active_subscription,
    require_company_access,
    require_store_admin,
)
from app.core.idempotency import IdempotencyEnforcer
from app.core.security import get_current_user, require_manager, resolve_tenant_company_id
from app.integrations.errors import ProviderNotConfiguredError
from app.models.user import User
from app.services.payment_providers import PaymentProviderResolver
from app.storage.payments_sql import PaymentIntentsStorageSQL, PaymentsStorageSQL, payment_intents
from app.storage.wallet_sql import WalletStorageSQL

logger = logging.getLogger(__name__)


async def _require_company_context(current_user: User = Depends(get_current_verified_user)) -> User:
    resolve_tenant_company_id(current_user, not_found_detail="Company not set")
    return current_user


async def _get_payment_storage(db: AsyncSession) -> PaymentsStorageSQL:
    try:
        return PaymentsStorageSQL(db)
    except ProviderNotConfiguredError as exc:
        raise HTTPException(status_code=503, detail=exc.code)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("payments storage init failed: %s", e)
        raise HTTPException(status_code=_pick_http_status(e), detail=str(e))


async def _get_wallet_storage(db: AsyncSession) -> WalletStorageSQL:
    try:
        return WalletStorageSQL(db)
    except ProviderNotConfiguredError as exc:
        raise HTTPException(status_code=503, detail=exc.code)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("wallet storage init failed: %s", e)
        raise HTTPException(status_code=_pick_http_status(e), detail=str(e))


async def _get_intents_storage(db: AsyncSession) -> PaymentIntentsStorageSQL:
    try:
        return PaymentIntentsStorageSQL(db)
    except ProviderNotConfiguredError as exc:
        raise HTTPException(status_code=503, detail=exc.code)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("payment intents storage init failed: %s", e)
        raise HTTPException(status_code=_pick_http_status(e), detail=str(e))


def _pick_http_status(exc: Exception) -> int:
    if isinstance(exc, HTTPException):
        raise exc
    msg = str(exc).lower()
    if "not found" in msg:
        return status.HTTP_404_NOT_FOUND
    if "insufficient" in msg and "fund" in msg:
        return status.HTTP_409_CONFLICT
    if "mismatch" in msg:
        return status.HTTP_409_CONFLICT
    if "unique" in msg or "duplicate" in msg or "already exist" in msg or "conflict" in msg:
        return status.HTTP_409_CONFLICT
    return status.HTTP_400_BAD_REQUEST


async def _ensure_user_in_company(
    target_user_id: int,
    current_user: User,
    db: AsyncSession,
    *,
    not_found_detail: str = "payment not found",
) -> User:
    resolved_company_id = resolve_tenant_company_id(current_user, not_found_detail="Company not set")
    user = await db.get(User, target_user_id)
    if not user:
        raise HTTPException(status_code=404, detail=not_found_detail)
    if getattr(user, "company_id", None) != resolved_company_id:
        raise HTTPException(status_code=404, detail=not_found_detail)
    return user


_BACKEND = "sql"


# --------- Schemas ----------
class CreatePaymentRequest(BaseModel):
    user_id: int = Field(..., ge=1)
    wallet_account_id: int = Field(..., ge=1)
    amount: Decimal = Field(..., gt=0)
    currency: str = Field(..., min_length=3, max_length=10)
    reference: str | None = Field(None, max_length=255)

    @field_validator("currency")
    def _upper(cls, v: str) -> str:
        return (v or "").strip().upper()


class Payment(BaseModel):
    id: int
    user_id: int
    wallet_account_id: int
    amount: Decimal
    currency: str
    status: str
    refund_amount: Decimal
    reference: str | None
    created_at: str
    updated_at: str


class RefundRequest(BaseModel):
    amount: Decimal = Field(..., gt=0)
    reference: str | None = Field(None, max_length=255)


class CancelRequest(BaseModel):
    reason: str | None = Field(None, max_length=255)


class PageMeta(BaseModel):
    page: int
    size: int
    total: int


class PaymentList(BaseModel):
    items: list[Payment]
    meta: PageMeta


class PaymentIntentCreate(BaseModel):
    amount: Decimal = Field(..., gt=0)
    currency: str = Field(..., min_length=3, max_length=10)
    customer_id: str = Field(..., min_length=1, max_length=128)
    metadata: dict[str, Any] | None = None


class PaymentIntentOut(BaseModel):
    id: str
    provider: str
    provider_version: int
    status: str
    amount: Decimal
    currency: str
    customer_id: str
    provider_intent_id: str
    metadata: dict[str, Any]
    created_at: str


async def _ensure_account_access(account_id: int, current_user: User, db: AsyncSession) -> dict:
    try:
        storage = await _get_wallet_storage(db)
        acc = await storage.get_account(account_id, company_id=getattr(current_user, "company_id", None))
        if not acc:
            raise HTTPException(status_code=404, detail="wallet account not found")
        await _ensure_user_in_company(
            int(acc.get("user_id", 0)), current_user, db, not_found_detail="wallet account not found"
        )
        return acc
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("wallet account access check failed: %s", e)
        raise HTTPException(status_code=_pick_http_status(e), detail=str(e))


async def _ensure_payment_visible(
    payment: dict[str, Any] | None, current_user: User, db: AsyncSession
) -> dict[str, Any]:
    if not payment:
        raise HTTPException(status_code=404, detail="payment not found")
    await _ensure_user_in_company(int(payment.get("user_id", 0)), current_user, db)
    return payment


# --------- Router ----------
router = APIRouter(prefix="/api/v1/payments", tags=["payments"])
admin_router = APIRouter(
    dependencies=[
        Depends(require_company_access),
        Depends(_require_company_context),
        Depends(require_store_admin),
        Depends(require_active_subscription),
    ]
)
manager_router = APIRouter(
    dependencies=[
        Depends(require_company_access),
        Depends(_require_company_context),
        Depends(require_manager),
        Depends(require_active_subscription),
    ]
)


@router.get("/health")
async def health():
    return {"status": "ok", "backend": _BACKEND}


def _extract_webhook_signature(headers: dict[str, str]) -> str | None:
    return headers.get("x-tiptop-signature") or headers.get("x-signature")


def _extract_webhook_timestamp(headers: dict[str, str]) -> str | None:
    return headers.get("x-tiptop-timestamp") or headers.get("x-timestamp")


def _extract_webhook_event_id(payload: dict[str, Any], body: bytes) -> str:
    for key in ("event_id", "id", "payment_id", "invoice_id", "refund_id"):
        value = payload.get(key)
        if value:
            return str(value)
    return hashlib.sha256(body).hexdigest()


async def _resolve_webhook_company_id(payload: dict[str, Any], db: AsyncSession) -> int | None:
    company_id = payload.get("company_id") or payload.get("companyId")
    if company_id is not None:
        try:
            return int(company_id)
        except Exception:
            return None

    provider_intent_id = payload.get("provider_intent_id") or payload.get("payment_intent_id")
    if provider_intent_id:
        row = (
            await db.execute(
                select(payment_intents.c.company_id).where(
                    payment_intents.c.provider_intent_id == str(provider_intent_id)
                )
            )
        ).scalar_one_or_none()
        if row is not None:
            return int(row)
    return None


@router.post("/webhooks/tiptop")
async def tiptop_webhook(
    request: Request,
    db: AsyncSession = Depends(get_async_db),
):
    body = await request.body()
    if not body:
        raise HTTPException(status_code=400, detail="empty_payload")

    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="invalid_json")

    if settings.is_production and (not settings.TIPTOP_API_SECRET or not settings.TIPTOP_API_KEY):
        raise HTTPException(status_code=503, detail="payment_provider_not_configured")

    signature = _extract_webhook_signature({k.lower(): v for k, v in request.headers.items()})
    if not signature:
        raise HTTPException(status_code=401, detail="missing_signature")

    if not settings.TIPTOP_API_SECRET:
        raise HTTPException(status_code=503, detail="payment_provider_not_configured")

    expected = hmac.new(settings.TIPTOP_API_SECRET.encode("utf-8"), body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        raise HTTPException(status_code=401, detail="invalid_signature")

    ts_header = _extract_webhook_timestamp({k.lower(): v for k, v in request.headers.items()})
    if ts_header:
        try:
            ts = int(ts_header)
        except Exception:
            raise HTTPException(status_code=401, detail="invalid_timestamp")
        if abs(int(time.time()) - ts) > 300:
            raise HTTPException(status_code=401, detail="invalid_timestamp")

    event_id = _extract_webhook_event_id(payload, body)
    company_id = await _resolve_webhook_company_id(payload, db)
    if company_id is None:
        company_id = 0

    enforcer = IdempotencyEnforcer(prefix="webhook", default_ttl=86400, env=settings.ENVIRONMENT)
    allowed, processed_status = await enforcer.reserve(
        db,
        company_id=int(company_id),
        key=f"tiptop:{event_id}",
        ttl_seconds=86400,
    )
    if not allowed:
        return {"ok": True, "duplicate": True, "status_code": processed_status}

    await enforcer.set_result(
        db,
        company_id=int(company_id),
        key=f"tiptop:{event_id}",
        status_code=200,
        ttl_seconds=86400,
    )
    return {"ok": True}


@manager_router.post(
    "/",
    response_model=Payment,
    status_code=status.HTTP_201_CREATED,
)
async def create_and_capture(
    req: CreatePaymentRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    try:
        resolved_company_id = resolve_tenant_company_id(current_user, not_found_detail="Company not set")
        await _ensure_user_in_company(req.user_id, current_user, db, not_found_detail="user not found")
        acc = await _ensure_account_access(req.wallet_account_id, current_user, db)
        if int(acc.get("user_id", 0)) != req.user_id:
            raise HTTPException(status_code=404, detail="wallet account not found")
        storage = await _get_payment_storage(db)
        p = await storage.create_and_capture(
            req.user_id,
            req.wallet_account_id,
            req.amount,
            req.currency,
            req.reference,
            company_id=resolved_company_id,
        )
        await db.commit()
        return Payment(**p)
    except HTTPException:
        raise
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=_pick_http_status(e), detail=str(e))


@manager_router.post(
    "/{payment_id}/refund",
    response_model=Payment,
)
async def refund(
    payment_id: int = Path(..., ge=1),
    req: RefundRequest = Body(...),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    try:
        resolved_company_id = resolve_tenant_company_id(current_user, not_found_detail="Company not set")
        storage = await _get_payment_storage(db)
        payment = await _ensure_payment_visible(
            await storage.get(payment_id, company_id=resolved_company_id),
            current_user,
            db,
        )
        await _ensure_account_access(int(payment.get("wallet_account_id", 0)), current_user, db)
        p = await storage.refund(
            payment_id,
            req.amount,
            req.reference,
            company_id=resolved_company_id,
        )
        await db.commit()
        return Payment(**p)
    except HTTPException:
        raise
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=_pick_http_status(e), detail=str(e))


@manager_router.post(
    "/{payment_id}/cancel",
    response_model=Payment,
)
async def cancel(
    payment_id: int = Path(..., ge=1),
    req: CancelRequest = Body(None),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    try:
        resolved_company_id = resolve_tenant_company_id(current_user, not_found_detail="Company not set")
        storage = await _get_payment_storage(db)
        payment = await _ensure_payment_visible(
            await storage.get(payment_id, company_id=resolved_company_id),
            current_user,
            db,
        )
        await _ensure_account_access(int(payment.get("wallet_account_id", 0)), current_user, db)
        p = await storage.cancel(
            payment_id,
            req.reason if req else None,
            company_id=resolved_company_id,
        )
        await db.commit()
        return Payment(**p)
    except HTTPException:
        raise
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=_pick_http_status(e), detail=str(e))


@admin_router.get("/{payment_id}", response_model=Payment)
async def get_payment(
    payment_id: int = Path(..., ge=1),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    storage = await _get_payment_storage(db)
    resolved_company_id = resolve_tenant_company_id(current_user, not_found_detail="Company not set")
    p = await _ensure_payment_visible(
        await storage.get(payment_id, company_id=resolved_company_id),
        current_user,
        db,
    )
    await _ensure_account_access(int(p.get("wallet_account_id", 0)), current_user, db)
    return Payment(**p)


@admin_router.get("/", response_model=PaymentList)
async def list_payments(
    user_id: int | None = Query(None, ge=1),
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=200),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    resolved_company_id = resolve_tenant_company_id(current_user, not_found_detail="Company not set")
    allowed_ids: list[int] | None = None
    if user_id is not None:
        await _ensure_user_in_company(user_id, current_user, db, not_found_detail="payment not found")
    stmt = select(User.id).where(User.company_id == resolved_company_id)
    result = (await db.execute(stmt)).all()
    allowed_ids = [int(r[0]) for r in result]
    if user_id is not None:
        allowed_ids = [uid for uid in allowed_ids if uid == user_id]
    if allowed_ids is not None and not allowed_ids:
        allowed_ids = [-1]
    storage = await _get_payment_storage(db)
    out = await storage.list(
        user_id,
        page,
        size,
        user_ids=allowed_ids,
        company_id=resolved_company_id,
    )
    items = [Payment(**i) for i in out["items"]]
    meta = PageMeta(page=page, size=size, total=len(items))
    return PaymentList(items=items, meta=meta)


@manager_router.post(
    "/intents",
    response_model=PaymentIntentOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_payment_intent(
    req: PaymentIntentCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    try:
        resolved_company_id = resolve_tenant_company_id(current_user, not_found_detail="Company not set")
        gateway = await PaymentProviderResolver.resolve(db, domain="payments", company_id=resolved_company_id)
        intent = await gateway.create_payment_intent(
            amount=req.amount,
            currency=req.currency,
            customer_id=req.customer_id,
            metadata=req.metadata or {},
        )
        storage = await _get_intents_storage(db)
        row = await storage.create_intent(intent, company_id=resolved_company_id)
        await db.commit()
        return PaymentIntentOut(
            id=row["id"],
            provider=row["provider"],
            provider_version=int(row.get("provider_version") or 0),
            status=row["status"],
            amount=Decimal(str(row["amount"])),
            currency=row["currency"],
            customer_id=row["customer_id"],
            provider_intent_id=row["provider_intent_id"],
            metadata=row.get("metadata", {}),
            created_at=row["created_at"],
        )
    except ProviderNotConfiguredError as exc:
        await db.rollback()
        raise HTTPException(status_code=503, detail=exc.code)
    except HTTPException:
        raise
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=_pick_http_status(e), detail=str(e))


@admin_router.get(
    "/intents/{intent_id}",
    response_model=PaymentIntentOut,
)
async def get_payment_intent(
    intent_id: str = Path(..., min_length=1),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    resolved_company_id = resolve_tenant_company_id(current_user, not_found_detail="Company not set")
    storage = await _get_intents_storage(db)
    row = await storage.get_intent(intent_id, company_id=resolved_company_id)
    if not row:
        raise HTTPException(status_code=404, detail="payment_intent_not_found")
    return PaymentIntentOut(
        id=row["id"],
        provider=row["provider"],
        provider_version=int(row.get("provider_version") or 0),
        status=row["status"],
        amount=Decimal(str(row["amount"])),
        currency=row["currency"],
        customer_id=row["customer_id"],
        provider_intent_id=row["provider_intent_id"],
        metadata=row.get("metadata", {}),
        created_at=row["created_at"],
    )


router.include_router(admin_router)
router.include_router(manager_router)
