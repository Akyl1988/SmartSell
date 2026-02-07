# app/api/v1/invoices.py
from __future__ import annotations

import logging
from datetime import UTC, datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field, condecimal, constr, field_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_async_db
from app.core.dependencies import require_active_subscription, require_store_admin
from app.core.exceptions import ConflictError
from app.core.security import decode_and_validate, is_token_revoked, resolve_tenant_company_id
from app.models.billing import Invoice, WalletBalance, WalletTransaction
from app.models.company import Company
from app.models.user import User

logger = logging.getLogger(__name__)
router = APIRouter(
    prefix="/api/v1/invoices",
    tags=["invoices"],
    dependencies=[Depends(require_active_subscription)],
)
http_bearer = HTTPBearer(auto_error=False)


class InvoiceCreate(BaseModel):
    amount: condecimal(max_digits=14, decimal_places=2, gt=0) = Field(..., description="Invoice amount")
    currency: constr(min_length=3, max_length=8) = Field("KZT", description="Currency code")  # type: ignore
    status: constr(min_length=3, max_length=32) | None = Field(None, description="Invoice status")  # type: ignore
    description: str | None = Field(None, max_length=1024)

    @field_validator("currency")
    def _normalize_currency(cls, v: str) -> str:
        return (v or "KZT").strip().upper()

    @field_validator("description", mode="before")
    def _normalize_description(cls, v: str | None) -> str | None:
        if v is None:
            return None
        vv = (v or "").strip()
        return vv or None

    @field_validator("status")
    def _validate_status(cls, v: str | None) -> str | None:
        if v is None:
            return None
        vv = (v or "").strip().lower()
        if vv not in {"draft", "issued"}:
            raise ValueError("status must be draft or issued")
        return vv


class InvoiceUpdate(BaseModel):
    amount: condecimal(max_digits=14, decimal_places=2, gt=0) | None = Field(None, description="Invoice amount")
    currency: constr(min_length=3, max_length=8) | None = Field(None, description="Currency code")  # type: ignore
    description: str | None = Field(None, max_length=1024)

    @field_validator("currency")
    def _normalize_currency(cls, v: str | None) -> str | None:
        if v is None:
            return None
        return (v or "").strip().upper() or None

    @field_validator("description", mode="before")
    def _normalize_description(cls, v: str | None) -> str | None:
        if v is None:
            return None
        vv = (v or "").strip()
        return vv or None


class InvoiceOut(BaseModel):
    id: int
    invoice_number: str
    company_id: int
    amount: Decimal
    currency: str
    status: str
    description: str | None = None
    created_at: datetime | None = None
    issued_at: datetime | None = None
    paid_at: datetime | None = None
    voided_at: datetime | None = None
    ledger_entry_id: int | None = None
    payment_ref: str | None = None

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
    await require_store_admin(user)
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
    company_id = resolve_tenant_company_id(user, not_found_detail="Company not set")

    await _ensure_company(db, company_id)

    number = await Invoice.generate_number_async(db, company_id=company_id)
    now = datetime.now(UTC)
    inv_status = (payload.status or "draft").strip().lower()
    now = datetime.now(UTC)
    inv = Invoice(
        company_id=company_id,
        invoice_number=number,
        invoice_type="standard",
        subtotal=Decimal(payload.amount),
        tax_amount=Decimal("0.00"),
        total_amount=Decimal(payload.amount),
        currency=(payload.currency or "KZT").upper(),
        status=inv_status,
        issue_date=now,
        due_date=None,
        issued_at=now if inv_status == "issued" else None,
        paid_at=None,
        notes=payload.description,
    )
    db.add(inv)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise ConflictError("Invoice number already exists", "DUPLICATE_INVOICE_NUMBER", http_status=409)
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
        issued_at=inv.issued_at,
        paid_at=inv.paid_at,
        voided_at=inv.voided_at,
        ledger_entry_id=inv.ledger_entry_id,
        payment_ref=inv.payment_ref,
    )


@router.get("", response_model=list[InvoiceOut])
async def list_invoices(
    db: AsyncSession = Depends(get_async_db),
    user: User = Depends(_auth_user),
):
    resolved_company = resolve_tenant_company_id(user, not_found_detail="Company not set")

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
            issued_at=inv.issued_at,
            paid_at=inv.paid_at,
            voided_at=inv.voided_at,
            ledger_entry_id=inv.ledger_entry_id,
            payment_ref=inv.payment_ref,
        )
        for inv in rows
    ]


@router.get("/{invoice_id}", response_model=InvoiceOut)
async def get_invoice(
    invoice_id: int,
    db: AsyncSession = Depends(get_async_db),
    user: User = Depends(_auth_user),
):
    resolved_company = resolve_tenant_company_id(user, not_found_detail="Company not set")

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
        issued_at=inv.issued_at,
        paid_at=inv.paid_at,
        voided_at=inv.voided_at,
        ledger_entry_id=inv.ledger_entry_id,
        payment_ref=inv.payment_ref,
    )


@router.put("/{invoice_id}", response_model=InvoiceOut)
async def update_invoice(
    invoice_id: int,
    payload: InvoiceUpdate,
    db: AsyncSession = Depends(get_async_db),
    user: User = Depends(_auth_user),
):
    resolved_company = resolve_tenant_company_id(user, not_found_detail="Company not set")

    stmt = select(Invoice).where(Invoice.id == invoice_id, Invoice.company_id == resolved_company)
    stmt = stmt.where(Invoice.deleted_at.is_(None)) if hasattr(Invoice, "deleted_at") else stmt
    inv = (await db.execute(stmt)).scalars().first()
    if not inv:
        raise HTTPException(status_code=404, detail="Invoice not found")

    if not inv.is_draft:
        raise ConflictError("Invoice can only be updated in draft status", "INVOICE_NOT_DRAFT", http_status=409)

    if payload.amount is not None:
        inv.subtotal = Decimal(payload.amount)
        inv.total_amount = Decimal(payload.amount)
    if payload.currency is not None:
        inv.currency = payload.currency
    if payload.description is not None:
        inv.notes = payload.description

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
        issued_at=inv.issued_at,
        paid_at=inv.paid_at,
        voided_at=inv.voided_at,
        ledger_entry_id=inv.ledger_entry_id,
        payment_ref=inv.payment_ref,
    )


@router.post("/{invoice_id}/issue", response_model=InvoiceOut)
async def issue_invoice(
    invoice_id: int,
    db: AsyncSession = Depends(get_async_db),
    user: User = Depends(_auth_user),
):
    resolved_company = resolve_tenant_company_id(user, not_found_detail="Company not set")

    stmt = select(Invoice).where(Invoice.id == invoice_id, Invoice.company_id == resolved_company)
    stmt = stmt.where(Invoice.deleted_at.is_(None)) if hasattr(Invoice, "deleted_at") else stmt
    inv = (await db.execute(stmt)).scalars().first()
    if not inv:
        raise HTTPException(status_code=404, detail="Invoice not found")

    if inv.is_void:
        raise ConflictError("Invoice is void", "INVOICE_VOID", http_status=409)
    if inv.is_paid:
        raise ConflictError("Invoice already paid", "INVOICE_PAID", http_status=409)
    if not inv.is_draft:
        raise ConflictError("Invoice already issued", "INVOICE_ALREADY_ISSUED", http_status=409)

    inv.mark_issued()
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
        issued_at=inv.issued_at,
        paid_at=inv.paid_at,
        voided_at=inv.voided_at,
        ledger_entry_id=inv.ledger_entry_id,
        payment_ref=inv.payment_ref,
    )


@router.post("/{invoice_id}/void", response_model=InvoiceOut)
async def void_invoice(
    invoice_id: int,
    db: AsyncSession = Depends(get_async_db),
    user: User = Depends(_auth_user),
):
    resolved_company = resolve_tenant_company_id(user, not_found_detail="Company not set")

    stmt = select(Invoice).where(Invoice.id == invoice_id, Invoice.company_id == resolved_company)
    stmt = stmt.where(Invoice.deleted_at.is_(None)) if hasattr(Invoice, "deleted_at") else stmt
    inv = (await db.execute(stmt)).scalars().first()
    if not inv:
        raise HTTPException(status_code=404, detail="Invoice not found")

    if inv.is_paid:
        raise ConflictError("Invoice already paid", "INVOICE_PAID", http_status=409)
    if inv.is_void:
        raise ConflictError("Invoice already void", "INVOICE_VOID", http_status=409)

    inv.mark_voided()
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
        issued_at=inv.issued_at,
        paid_at=inv.paid_at,
        voided_at=inv.voided_at,
        ledger_entry_id=inv.ledger_entry_id,
        payment_ref=inv.payment_ref,
    )


@router.post("/{invoice_id}/pay", response_model=InvoiceOut)
async def pay_invoice(
    invoice_id: int,
    request: Request,
    db: AsyncSession = Depends(get_async_db),
    user: User = Depends(_auth_user),
):
    resolved_company = resolve_tenant_company_id(user, not_found_detail="Company not set")
    request_id = (request.headers.get("X-Request-Id") or "").strip() or None

    stmt = select(Invoice).where(Invoice.id == invoice_id, Invoice.company_id == resolved_company)
    stmt = stmt.where(Invoice.deleted_at.is_(None)) if hasattr(Invoice, "deleted_at") else stmt
    inv = (await db.execute(stmt)).scalars().first()
    if not inv:
        raise HTTPException(status_code=404, detail="Invoice not found")

    if inv.is_void:
        raise ConflictError("Invoice is void", "INVOICE_VOID", http_status=409)
    if inv.is_paid:
        if request_id and inv.payment_ref == request_id:
            return InvoiceOut(
                id=inv.id,
                invoice_number=inv.invoice_number,
                company_id=inv.company_id,
                amount=inv.total_amount,
                currency=inv.currency,
                status=inv.status,
                description=inv.notes,
                created_at=inv.issue_date,
                issued_at=inv.issued_at,
                paid_at=inv.paid_at,
                voided_at=inv.voided_at,
                ledger_entry_id=inv.ledger_entry_id,
                payment_ref=inv.payment_ref,
            )
        raise ConflictError("Invoice already paid", "INVOICE_PAID", http_status=409)
    if not inv.is_issued:
        raise ConflictError("Invoice must be issued before payment", "INVOICE_NOT_ISSUED", http_status=409)

    wallet = await WalletBalance.get_for_company_async(
        db,
        resolved_company,
        create_if_missing=True,
        currency=inv.currency,
    )

    if request_id:
        existing = (
            (
                await db.execute(
                    select(WalletTransaction).where(
                        WalletTransaction.wallet_id == wallet.id,
                        WalletTransaction.client_request_id == request_id,
                    )
                )
            )
            .scalars()
            .first()
        )
        if existing:
            inv.ledger_entry_id = existing.id
            inv.payment_ref = request_id
            inv.mark_paid()
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
                issued_at=inv.issued_at,
                paid_at=inv.paid_at,
                voided_at=inv.voided_at,
                ledger_entry_id=inv.ledger_entry_id,
                payment_ref=inv.payment_ref,
            )

    amount = inv.total_amount
    before = wallet.balance or Decimal("0")
    if before < amount:
        raise HTTPException(status_code=400, detail="Insufficient wallet balance")

    wallet.balance = before - amount
    trx = WalletTransaction(
        wallet_id=wallet.id,
        transaction_type="debit",
        amount=amount,
        balance_before=before,
        balance_after=wallet.balance,
        description="invoice_payment",
        reference_type="invoice",
        reference_id=inv.id,
        client_request_id=request_id,
    )
    db.add(trx)
    await db.flush()

    inv.ledger_entry_id = trx.id
    inv.payment_ref = request_id
    inv.mark_paid()

    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        existing = (
            (
                await db.execute(
                    select(WalletTransaction).where(
                        WalletTransaction.wallet_id == wallet.id,
                        WalletTransaction.client_request_id == request_id,
                    )
                )
            )
            .scalars()
            .first()
        )
        if existing:
            inv.ledger_entry_id = existing.id
            inv.payment_ref = request_id
            inv.mark_paid()
            await db.commit()
        else:
            raise

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
        issued_at=inv.issued_at,
        paid_at=inv.paid_at,
        voided_at=inv.voided_at,
        ledger_entry_id=inv.ledger_entry_id,
        payment_ref=inv.payment_ref,
    )
