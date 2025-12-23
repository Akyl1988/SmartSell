# app/api/v1/payments.py
from __future__ import annotations

import logging
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Body, Depends, HTTPException, Path, Query, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.security import get_current_user as get_current_user_security
from app.models.user import User

logger = logging.getLogger(__name__)


async def _auth_user(
    token_data: dict = Depends(get_current_user_security),
    db: AsyncSession = Depends(get_db),
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


# Storage
try:
    from app.storage.payments_sql import PaymentsStorageSQL

    storage = PaymentsStorageSQL()
    _BACKEND = "sql"
except Exception as e:
    raise RuntimeError(f"Payments storage init failed: {e}")


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
    amount: str
    currency: str
    status: str
    refund_amount: str
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


# --------- Router ----------
router = APIRouter(prefix="/api/v1/payments", tags=["payments"])


@router.get("/health")
async def health():
    return {"status": "ok", "backend": _BACKEND}


@router.post(
    "/",
    response_model=Payment,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_role("admin", "manager"))],
)
async def create_and_capture(req: CreatePaymentRequest):
    try:
        p = storage.create_and_capture(
            req.user_id, req.wallet_account_id, req.amount, req.currency, req.reference
        )
        return Payment(**p)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post(
    "/{payment_id}/refund",
    response_model=Payment,
    dependencies=[Depends(require_role("admin", "manager"))],
)
async def refund(payment_id: int = Path(..., ge=1), req: RefundRequest = Body(...)):
    try:
        p = storage.refund(payment_id, req.amount, req.reference)
        return Payment(**p)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post(
    "/{payment_id}/cancel",
    response_model=Payment,
    dependencies=[Depends(require_role("admin", "manager"))],
)
async def cancel(payment_id: int = Path(..., ge=1), req: CancelRequest = Body(None)):
    try:
        p = storage.cancel(payment_id, req.reason if req else None)
        return Payment(**p)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/{payment_id}", response_model=Payment)
async def get_payment(payment_id: int = Path(..., ge=1)):
    p = storage.get(payment_id)
    if not p:
        raise HTTPException(status_code=404, detail="payment not found")
    return Payment(**p)


@router.get("/", response_model=PaymentList)
async def list_payments(
    user_id: int | None = Query(None, ge=1),
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=200),
):
    out = storage.list(user_id, page, size)
    return PaymentList(
        items=[Payment(**i) for i in out["items"]],
        meta=PageMeta(**out["meta"]),
    )
