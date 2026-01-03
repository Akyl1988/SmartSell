# app/api/v1/payments.py
from __future__ import annotations

import logging
from collections.abc import Callable
from decimal import Decimal
from typing import Any, TypeVar

from fastapi import APIRouter, Body, Depends, HTTPException, Path, Query, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.core.db import get_async_db
from app.core.security import get_current_user as get_current_user_security
from app.models.user import User

logger = logging.getLogger(__name__)
T = TypeVar("T")


async def _auth_user(
    token_data: dict = Depends(get_current_user_security),
    db: AsyncSession = Depends(get_async_db),
) -> User:
    sub = token_data.get("sub")
    try:
        user_id = int(sub)
    except Exception:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")

    user = await db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    return user


def require_role(*roles: str):
    async def dep(user: User = Depends(_auth_user)):
        if getattr(user, "role", None) not in roles:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="forbidden")
        return user

    return dep


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


def _is_platform_admin(user: User | None) -> bool:
    try:
        return str(getattr(user, "role", "")).lower() == "platform_admin"
    except Exception:
        return False


async def _ensure_user_in_company(
    target_user_id: int,
    current_user: User,
    db: AsyncSession,
    *,
    not_found_detail: str = "payment not found",
) -> User:
    user = await db.get(User, target_user_id)
    if not user:
        raise HTTPException(status_code=404, detail=not_found_detail)
    if _is_platform_admin(current_user):
        return user
    if getattr(user, "company_id", None) != getattr(current_user, "company_id", None):
        raise HTTPException(status_code=404, detail=not_found_detail)
    return user


# Storage (lazy to avoid import-time failures)
_BACKEND = "sql"


def _init_payment_storage(sync_db: Session):
    from app.storage.payments_sql import PaymentsStorageSQL

    return PaymentsStorageSQL(sync_db)


def _init_wallet_storage(sync_db: Session):
    from app.storage.wallet_sql import WalletStorageSQL

    return WalletStorageSQL(sync_db)


async def _with_payment_storage(db: AsyncSession, fn: Callable[[Any], T]) -> T:
    try:

        def _run(sync_session: Session):
            storage = _init_payment_storage(sync_session)
            return fn(storage)

        return await db.run_sync(_run)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("payments storage call failed: %s", e)
        raise HTTPException(status_code=_pick_http_status(e), detail=str(e))


async def _with_wallet_storage(db: AsyncSession, fn: Callable[[Any], T]) -> T:
    try:

        def _run(sync_session: Session):
            storage = _init_wallet_storage(sync_session)
            return fn(storage)

        return await db.run_sync(_run)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("wallet storage init failed for payments API: %s", e)
        raise HTTPException(status_code=_pick_http_status(e), detail=str(e))


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


async def _ensure_account_access(account_id: int, current_user: User, db: AsyncSession) -> dict:
    acc = await _with_wallet_storage(
        db, lambda storage: storage.get_account(account_id, company_id=getattr(current_user, "company_id", None))
    )
    if not acc:
        raise HTTPException(status_code=404, detail="wallet account not found")
    await _ensure_user_in_company(
        int(acc.get("user_id", 0)), current_user, db, not_found_detail="wallet account not found"
    )
    return acc


async def _ensure_payment_visible(
    payment: dict[str, Any] | None, current_user: User, db: AsyncSession
) -> dict[str, Any]:
    if not payment:
        raise HTTPException(status_code=404, detail="payment not found")
    await _ensure_user_in_company(int(payment.get("user_id", 0)), current_user, db)
    return payment


# --------- Router ----------
router = APIRouter(prefix="/api/v1/payments", tags=["payments"])


@router.get("/health")
async def health():
    return {"status": "ok", "backend": _BACKEND}


@router.post(
    "/",
    response_model=Payment,
    status_code=status.HTTP_201_CREATED,
)
async def create_and_capture(
    req: CreatePaymentRequest,
    current_user: User = Depends(require_role("admin", "manager")),
    db: AsyncSession = Depends(get_async_db),
):
    try:
        await _ensure_user_in_company(req.user_id, current_user, db, not_found_detail="user not found")
        acc = await _ensure_account_access(req.wallet_account_id, current_user, db)
        if int(acc.get("user_id", 0)) != req.user_id:
            raise HTTPException(status_code=404, detail="wallet account not found")
        p = await _with_payment_storage(
            db,
            lambda storage: storage.create_and_capture(
                req.user_id,
                req.wallet_account_id,
                req.amount,
                req.currency,
                req.reference,
                company_id=getattr(current_user, "company_id", None),
            ),
        )
        await db.commit()
        return Payment(**p)
    except HTTPException:
        raise
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=_pick_http_status(e), detail=str(e))


@router.post(
    "/{payment_id}/refund",
    response_model=Payment,
)
async def refund(
    payment_id: int = Path(..., ge=1),
    req: RefundRequest = Body(...),
    current_user: User = Depends(require_role("admin", "manager")),
    db: AsyncSession = Depends(get_async_db),
):
    try:
        payment = await _ensure_payment_visible(
            await _with_payment_storage(
                db, lambda storage: storage.get(payment_id, company_id=getattr(current_user, "company_id", None))
            ),
            current_user,
            db,
        )
        await _ensure_account_access(int(payment.get("wallet_account_id", 0)), current_user, db)
        p = await _with_payment_storage(
            db,
            lambda storage: storage.refund(
                payment_id,
                req.amount,
                req.reference,
                company_id=getattr(current_user, "company_id", None),
            ),
        )
        await db.commit()
        return Payment(**p)
    except HTTPException:
        raise
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=_pick_http_status(e), detail=str(e))


@router.post(
    "/{payment_id}/cancel",
    response_model=Payment,
)
async def cancel(
    payment_id: int = Path(..., ge=1),
    req: CancelRequest = Body(None),
    current_user: User = Depends(require_role("admin", "manager")),
    db: AsyncSession = Depends(get_async_db),
):
    try:
        payment = await _ensure_payment_visible(
            await _with_payment_storage(
                db, lambda storage: storage.get(payment_id, company_id=getattr(current_user, "company_id", None))
            ),
            current_user,
            db,
        )
        await _ensure_account_access(int(payment.get("wallet_account_id", 0)), current_user, db)
        p = await _with_payment_storage(
            db,
            lambda storage: storage.cancel(
                payment_id,
                req.reason if req else None,
                company_id=getattr(current_user, "company_id", None),
            ),
        )
        await db.commit()
        return Payment(**p)
    except HTTPException:
        raise
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=_pick_http_status(e), detail=str(e))


@router.get("/{payment_id}", response_model=Payment)
async def get_payment(
    payment_id: int = Path(..., ge=1),
    current_user: User = Depends(_auth_user),
    db: AsyncSession = Depends(get_async_db),
):
    p = await _ensure_payment_visible(
        await _with_payment_storage(
            db, lambda storage: storage.get(payment_id, company_id=getattr(current_user, "company_id", None))
        ),
        current_user,
        db,
    )
    await _ensure_account_access(int(p.get("wallet_account_id", 0)), current_user, db)
    return Payment(**p)


@router.get("/", response_model=PaymentList)
async def list_payments(
    user_id: int | None = Query(None, ge=1),
    company_id: int | None = Query(None, ge=1),
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=200),
    current_user: User = Depends(_auth_user),
    db: AsyncSession = Depends(get_async_db),
):
    allowed_ids: list[int] | None = None
    if company_id is not None and company_id != getattr(current_user, "company_id", None):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="forbidden")
    if user_id is not None:
        await _ensure_user_in_company(user_id, current_user, db, not_found_detail="payment not found")
    if not _is_platform_admin(current_user):
        stmt = select(User.id).where(User.company_id == getattr(current_user, "company_id", None))
        result = (await db.execute(stmt)).all()
        allowed_ids = [int(r[0]) for r in result]
        if user_id is not None:
            allowed_ids = [uid for uid in allowed_ids if uid == user_id]
        if allowed_ids is not None and not allowed_ids:
            allowed_ids = [-1]
    out = await _with_payment_storage(
        db,
        lambda storage: storage.list(
            user_id,
            page,
            size,
            user_ids=allowed_ids,
            company_id=getattr(current_user, "company_id", None),
        ),
    )
    items = [Payment(**i) for i in out["items"]]
    if not _is_platform_admin(current_user):
        ids = {p.user_id for p in items}
        allowed_map: dict[int, Any] = {}
        if ids:
            stmt = select(User.id, User.company_id).where(User.id.in_(ids))
            rows = (await db.execute(stmt)).all()
            allowed_map = {int(r[0]): r[1] for r in rows}
        items = [p for p in items if allowed_map.get(p.user_id) == getattr(current_user, "company_id", None)]
        meta = PageMeta(page=page, size=size, total=len(items))
    else:
        meta = PageMeta(**out["meta"])
    return PaymentList(items=items, meta=meta)
