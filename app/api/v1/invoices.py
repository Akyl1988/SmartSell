# app/api/v1/invoices.py
from __future__ import annotations

import logging
from datetime import UTC, datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field, condecimal, constr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_async_db
from app.core.security import decode_and_validate, is_token_revoked, resolve_tenant_company_id
from app.models.billing import Invoice
from app.models.company import Company
from app.models.user import User

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/invoices", tags=["invoices"])
http_bearer = HTTPBearer(auto_error=False)


class InvoiceCreate(BaseModel):
    amount: condecimal(max_digits=14, decimal_places=2) = Field(..., description="Invoice amount")
    currency: constr(min_length=3, max_length=8) = Field("KZT", description="Currency code")  # type: ignore
    status: constr(min_length=3, max_length=32) | None = Field(None, description="Invoice status")  # type: ignore
    description: str | None = Field(None, max_length=1024)


class InvoiceOut(BaseModel):
    id: int
    invoice_number: str
    company_id: int
    amount: Decimal
    currency: str
    status: str
    description: str | None = None
    created_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


async def _auth_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(http_bearer),
    db: AsyncSession = Depends(get_async_db),
) -> User:
    if not credentials or credentials.scheme.lower() != "bearer":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")

    try:
        payload = decode_and_validate(credentials.credentials, expected_type="access")
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(e))

    jti = payload.get("jti")
    if jti and is_token_revoked(jti):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token revoked")

    try:
        user_id = int(payload.get("sub"))
    except Exception:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")

    user = await db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    return user


async def _ensure_company(db: AsyncSession, company_id: int) -> Company:
    company = await db.get(Company, company_id)
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")
    return company


@router.post("", response_model=InvoiceOut, status_code=status.HTTP_201_CREATED)
async def create_invoice(
    payload: InvoiceCreate,
    db: AsyncSession = Depends(get_async_db),
    user: User = Depends(_auth_user),
):
    company_id = resolve_tenant_company_id(
        user, getattr(payload, "company_id", None), not_found_detail="Company not set"
    )

    await _ensure_company(db, company_id)

    number = await Invoice.generate_number_async(db, company_id=company_id)
    now = datetime.now(UTC)
    inv = Invoice(
        company_id=company_id,
        invoice_number=number,
        invoice_type="standard",
        subtotal=Decimal(payload.amount),
        tax_amount=Decimal("0.00"),
        total_amount=Decimal(payload.amount),
        currency=(payload.currency or "KZT").upper(),
        status=(payload.status or "draft").strip(),
        issue_date=now,
        due_date=None,
        paid_at=None,
        notes=payload.description,
    )
    db.add(inv)
    await db.commit()
    await db.refresh(inv)
    return InvoiceOut(
        id=inv.id,
        invoice_number=inv.invoice_number,
        company_id=inv.company_id,
        amount=inv.total_amount,
        currency=inv.currency,
        status=inv.status,
        description=inv.notes,
        created_at=inv.issue_date,
    )


@router.get("", response_model=list[InvoiceOut])
async def list_invoices(
    db: AsyncSession = Depends(get_async_db),
    user: User = Depends(_auth_user),
):
    resolved_company = resolve_tenant_company_id(user, company_id, not_found_detail="Company not set")

    stmt = select(Invoice).where(Invoice.company_id == resolved_company)
    stmt = stmt.where(Invoice.deleted_at.is_(None)) if hasattr(Invoice, "deleted_at") else stmt
    rows = (await db.execute(stmt.order_by(Invoice.id.desc()))).scalars().all()
    return [
        InvoiceOut(
            id=inv.id,
            invoice_number=inv.invoice_number,
            company_id=inv.company_id,
            amount=inv.total_amount,
            currency=inv.currency,
            status=inv.status,
            description=inv.notes,
            created_at=inv.issue_date,
        )
        for inv in rows
    ]


@router.get("/{invoice_id}", response_model=InvoiceOut)
async def get_invoice(
    invoice_id: int,
    db: AsyncSession = Depends(get_async_db),
    user: User = Depends(_auth_user),
):
    resolved_company = resolve_tenant_company_id(user, company_id, not_found_detail="Company not set")

    stmt = select(Invoice).where(Invoice.id == invoice_id, Invoice.company_id == resolved_company)
    stmt = stmt.where(Invoice.deleted_at.is_(None)) if hasattr(Invoice, "deleted_at") else stmt
    inv = (await db.execute(stmt)).scalars().first()
    if not inv:
        raise HTTPException(status_code=404, detail="Invoice not found")

    return InvoiceOut(
        id=inv.id,
        invoice_number=inv.invoice_number,
        company_id=inv.company_id,
        amount=inv.total_amount,
        currency=inv.currency,
        status=inv.status,
        description=inv.notes,
        created_at=inv.issue_date,
    )
