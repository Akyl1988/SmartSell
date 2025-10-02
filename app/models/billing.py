# app/models/billing.py
from __future__ import annotations

"""
Биллинг и кошелёк.
- Все timestamp'ы храним в UTC. Для UI есть helper `to_astana()`.
- Добавлены унифицированные конвертеры в OperationRow для: BillingPayment, Invoice, BillingInvoice, WalletTransaction.
- BillingInvoice расширен полями: total_due, paid_amount + расчётные методы.
- Кошелёк: безопасные debit/credit, авто-дотоп для дефицита, settle_amount_with_wallet (+ async).
- Подписка: forward-only биллинг, расчёт next_billing_date.
- Универсальная лента денежных операций (sync/async) по компании с сортировкой по дате.
"""

import enum
import json
import logging
from contextlib import asynccontextmanager, contextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from typing import (
    Any,
    Dict,
    Optional,
    List,
    Tuple,
    TYPE_CHECKING,
    Iterator,
    AsyncIterator,
    Sequence,
    ClassVar,
    Iterable,
    Literal,
)

from zoneinfo import ZoneInfo
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    select,
    and_,
    or_,
)
from sqlalchemy.exc import DBAPIError, OperationalError
from sqlalchemy.orm import (
    Mapped,
    mapped_column,
    relationship,
    validates,
    Session,
    backref,
)
from sqlalchemy.orm.exc import StaleDataError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.base import BaseModel, SoftDeleteMixin

if TYPE_CHECKING:
    from app.models.company import Company
    from app.models.order import Order

log = logging.getLogger(__name__)

ASTANA_TZ = ZoneInfo("Asia/Almaty")  # UI: «Астана, Казахстан (UTC+5 / GMT+5)»


# ---------------- utils ----------------
def utc_now() -> datetime:
    return datetime.now(UTC)


def to_astana(dt: Optional[datetime]) -> Optional[datetime]:
    if not dt:
        return None
    try:
        return dt.astimezone(ASTANA_TZ)
    except Exception:
        return dt


def _to_decimal(v: Any) -> Decimal:
    if v is None:
        return Decimal("0.00")
    if isinstance(v, Decimal):
        return v.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    try:
        return Decimal(str(v)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except Exception:
        return Decimal("0.00")


def _json_dumps(data: Any) -> Optional[str]:
    if data is None:
        return None
    try:
        return json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    except Exception:
        return None


def _safe_iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.isoformat() if dt else None


def _norm_currency_code(v: Optional[str], *, default: str = "KZT") -> Optional[str]:
    vv = (v or "").strip().upper()
    return (vv or default).upper() if default else (vv or None)


# ---------------- enums ----------------
class PaymentStatus(str, enum.Enum):
    PENDING = "pending"
    AUTHORIZED = "authorized"
    CAPTURED = "captured"
    PARTIAL = "partial"
    REFUNDED = "refunded"
    VOIDED = "voided"
    FAILED = "failed"


class PaymentMethod(str, enum.Enum):
    CARD = "card"
    CASH = "cash"
    BANK_TRANSFER = "bank_transfer"
    WALLET = "wallet"
    OTHER = "other"


# ======================================================================
# BillingPayment
# ======================================================================
class BillingPayment(BaseModel, SoftDeleteMixin):
    __tablename__ = "billing_payments"
    __allow_unmapped__ = True

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)

    order_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"), nullable=True, index=True
    )
    company_id: Mapped[int] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    subscription_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("subscriptions.id", ondelete="CASCADE"), nullable=True, index=True
    )

    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=0)
    currency: Mapped[str] = mapped_column(String(8), nullable=False, default="KZT")

    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=PaymentStatus.PENDING.value, index=True
    )
    method: Mapped[str] = mapped_column(
        String(32), nullable=False, default=PaymentMethod.OTHER.value, index=True
    )

    provider: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    provider_payment_id: Mapped[Optional[str]] = mapped_column(String(128), index=True)
    provider_receipt_url: Mapped[Optional[str]] = mapped_column(String(1024))

    authorized_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), index=True)
    captured_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), index=True)
    refunded_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), index=True)
    failed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), index=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False, index=True
    )

    description: Mapped[Optional[str]] = mapped_column(Text)
    meta: Mapped[Optional[str]] = mapped_column(Text)

    order: Mapped[Optional["Order"]] = relationship(
        "Order", back_populates="payments", foreign_keys=lambda: [BillingPayment.order_id]
    )
    company: Mapped[Optional[Any]] = relationship("Company", back_populates="billing_payments")
    subscription: Mapped[Optional["Subscription"]] = relationship(
        "Subscription",
        back_populates="payments",
        foreign_keys=lambda: [BillingPayment.subscription_id],
    )

    __table_args__ = (
        Index("ix_billing_payments_status_created", "status", "created_at"),
        Index("ix_billing_payments_method_created", "method", "created_at"),
        Index("ix_billing_payments_company_created", "company_id", "created_at"),
        CheckConstraint("amount >= 0", name="ck_bp_amount_non_negative"),
        UniqueConstraint("provider", "provider_payment_id", name="uq_bp_provider_payment"),
        {"extend_existing": True},
    )

    # -------- нормализация --------
    @validates("currency")
    def _norm_currency(self, _k: str, v: Optional[str]) -> Optional[str]:
        return _norm_currency_code(v, default="KZT")

    @validates("status")
    def _norm_status(self, _k: str, v: Optional[str]) -> Optional[str]:
        vv = (v or "").strip().lower()
        allowed = {s.value for s in PaymentStatus}
        if vv and vv not in allowed:
            raise ValueError(f"Invalid payment status: {vv}")
        return vv or None

    @validates("method")
    def _norm_method(self, _k: str, v: Optional[str]) -> Optional[str]:
        vv = (v or "").strip().lower()
        allowed = {m.value for m in PaymentMethod}
        if vv and vv not in allowed:
            raise ValueError(f"Invalid payment method: {vv}")
        return vv or None

    @validates("provider")
    def _strip_provider(self, _k: str, v: Optional[str]) -> Optional[str]:
        return (v or "").strip() or None

    # -------- свойства/методы статуса --------
    @property
    def is_success(self) -> bool:
        return (self.status or "") in {
            PaymentStatus.CAPTURED.value,
            PaymentStatus.REFUNDED.value,
            PaymentStatus.PARTIAL.value,
        }

    @property
    def is_refunded(self) -> bool:
        return (self.status or "") == PaymentStatus.REFUNDED.value

    def mark_authorized(self, *, at: Optional[datetime] = None) -> None:
        self.status = PaymentStatus.AUTHORIZED.value
        self.authorized_at = at or utc_now()

    def mark_captured(self, *, at: Optional[datetime] = None) -> None:
        self.status = PaymentStatus.CAPTURED.value
        self.captured_at = at or utc_now()

    def mark_partial(self, *, at: Optional[datetime] = None) -> None:
        self.status = PaymentStatus.PARTIAL.value
        self.captured_at = at or utc_now()

    def mark_refunded(self, *, at: Optional[datetime] = None) -> None:
        self.status = PaymentStatus.REFUNDED.value
        self.refunded_at = at or utc_now()

    def mark_failed(self, *, at: Optional[datetime] = None, desc: Optional[str] = None) -> None:
        self.status = PaymentStatus.FAILED.value
        self.failed_at = at or utc_now()
        if desc:
            self.description = f"{(self.description or '').strip()} | {desc}".strip(" |")

    def mark_voided(self, *, at: Optional[datetime] = None, desc: Optional[str] = None) -> None:
        self.status = PaymentStatus.VOIDED.value
        self.failed_at = at or utc_now()
        if desc:
            self.description = f"{(self.description or '').strip()} | voided: {desc}".strip(" |")

    def link_receipt(self, url: Optional[str]) -> None:
        self.provider_receipt_url = (url or "").strip() or None

    def attach_to_invoice(self, session: Session, invoice: "Invoice") -> None:
        """Привязать к инвойсу и отметить его оплаченным."""
        if invoice.company_id != self.company_id:
            raise ValueError("Company mismatch: payment vs invoice")
        invoice.payment_id = self.id
        invoice.mark_paid()
        session.flush()

    def maybe_affect_order_status(
        self, session: Session, *, set_paid_on_capture: bool = True
    ) -> None:
        try:
            from app.models.order import Order  # lazy
        except Exception:
            return
        if not self.order_id:
            return
        order: Optional["Order"] = session.get(Order, self.order_id)
        if not order:
            return
        if set_paid_on_capture and self.status == PaymentStatus.CAPTURED.value and not getattr(order, "is_closed", False):
            try:
                order.mark_paid(note="auto by BillingPayment.capture", session=session)
            except Exception:
                pass

    # -------- фабрика/экспорт/аналитика --------
    @classmethod
    def factory(
        cls,
        *,
        company_id: int,
        amount: Decimal | int | float = 100,
        currency: str = "KZT",
        order_id: Optional[int] = None,
        subscription_id: Optional[int] = None,
        status: PaymentStatus = PaymentStatus.PENDING,
        method: PaymentMethod = PaymentMethod.OTHER,
        provider: Optional[str] = None,
        provider_payment_id: Optional[str] = None,
        description: Optional[str] = None,
        meta: Optional[Dict[str, Any]] = None,
    ) -> "BillingPayment":
        return cls(
            company_id=company_id,
            order_id=order_id,
            subscription_id=subscription_id,
            amount=_to_decimal(amount),
            currency=_norm_currency_code(currency, default="KZT"),
            status=status.value,
            method=method.value,
            provider=(provider or None),
            provider_payment_id=(provider_payment_id or None),
            description=description,
            meta=_json_dumps(meta),
        )

    @staticmethod
    def export_query(
        *,
        company_id: Optional[int] = None,
        status_in: Optional[Sequence[str]] = None,
        method_in: Optional[Sequence[str]] = None,
        date_from: Optional[datetime] = None,
        date_to: Optional[datetime] = None,
        min_amount: Optional[Decimal] = None,
        max_amount: Optional[Decimal] = None,
        order_id: Optional[int] = None,
    ):
        conds = []
        if company_id is not None:
            conds.append(BillingPayment.company_id == company_id)
        if status_in:
            conds.append(BillingPayment.status.in_(list(status_in)))
        if method_in:
            conds.append(BillingPayment.method.in_(list(method_in)))
        if order_id is not None:
            conds.append(BillingPayment.order_id == order_id)
        if date_from:
            conds.append(BillingPayment.created_at >= date_from)
        if date_to:
            conds.append(BillingPayment.created_at < date_to)
        if min_amount is not None:
            conds.append(BillingPayment.amount >= _to_decimal(min_amount))
        if max_amount is not None:
            conds.append(BillingPayment.amount <= _to_decimal(max_amount))

        return (
            select(
                BillingPayment.id,
                BillingPayment.order_id,
                BillingPayment.company_id,
                BillingPayment.subscription_id,
                BillingPayment.amount,
                BillingPayment.currency,
                BillingPayment.status,
                BillingPayment.method,
                BillingPayment.provider,
                BillingPayment.provider_payment_id,
                BillingPayment.provider_receipt_url,
                BillingPayment.created_at,
                BillingPayment.updated_at,
                BillingPayment.description,
            )
            .where(*conds if conds else [True])
            .order_by(BillingPayment.created_at.desc())
        )

    @staticmethod
    async def export_as_dicts_async(session: AsyncSession, **kwargs) -> List[Dict[str, Any]]:
        rows = (await session.execute(BillingPayment.export_query(**kwargs))).all()
        out: List[Dict[str, Any]] = []
        for r in rows:
            (
                pid,
                order_id,
                company_id,
                subscription_id,
                amount,
                currency,
                status,
                method,
                provider,
                provider_payment_id,
                receipt_url,
                created_at,
                updated_at,
                description,
            ) = r
            out.append(
                {
                    "payment_id": pid,
                    "order_id": order_id,
                    "company_id": company_id,
                    "subscription_id": subscription_id,
                    "amount": str(amount) if amount is not None else None,
                    "currency": currency,
                    "status": status,
                    "method": method,
                    "provider": provider,
                    "provider_payment_id": provider_payment_id,
                    "receipt_url": receipt_url,
                    "created_at": _safe_iso(created_at),
                    "updated_at": _safe_iso(updated_at),
                    "description": description,
                }
            )
        return out

    @staticmethod
    async def analytics_by_status_async(
        session: AsyncSession,
        *,
        company_id: Optional[int] = None,
        date_from: Optional[datetime] = None,
        date_to: Optional[datetime] = None,
    ) -> List[Dict[str, Any]]:
        conds = []
        if company_id is not None:
            conds.append(BillingPayment.company_id == company_id)
        if date_from:
            conds.append(BillingPayment.created_at >= date_from)
        if date_to:
            conds.append(BillingPayment.created_at < date_to)

        rows = await session.execute(
            select(
                BillingPayment.status,
                func.count(BillingPayment.id),
                func.coalesce(func.sum(BillingPayment.amount), 0),
            )
            .where(*conds if conds else [True])
            .group_by(BillingPayment.status)
            .order_by(BillingPayment.status.asc())
        )
        return [
            {"status": st, "count": int(cnt or 0), "amount_sum": str(total)}
            for (st, cnt, total) in rows.all()
        ]

    # -------- unified OperationRow (для ленты «Денежные операции») --------
    def to_operation_row(self) -> Dict[str, Any]:
        """
        Единый формат для UI:
        {
          "kind": "payment",
          "id": <int>,
          "direction": "out" | "in",
          "amount": "123.45",
          "currency": "KZT",
          "status": "...",
          "title": "Оплата (card)",
          "created_at": "...",
          "completed_at": "...",
          "links": {"receipt": "..."},
        }
        """
        direction = "out"  # платеж — расход
        completed = self.captured_at or self.refunded_at or self.failed_at or self.updated_at
        return {
            "kind": "payment",
            "id": self.id,
            "direction": direction,
            "amount": str(self.amount),
            "currency": _norm_currency_code(self.currency, default="KZT"),
            "status": self.status,
            "method": self.method,
            "title": f"Оплата ({self.method})",
            "created_at": _safe_iso(self.created_at),
            "completed_at": _safe_iso(completed),
            "links": {"receipt": self.provider_receipt_url} if self.provider_receipt_url else {},
            "is_success": self.is_success,
        }


# ======================================================================
# Subscription
# ======================================================================
class Subscription(BaseModel, SoftDeleteMixin):
    __tablename__ = "subscriptions"
    __allow_unmapped__ = True

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)

    company_id: Mapped[int] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True
    )

    plan: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)  # active|canceled|overdue|trial|paused
    billing_cycle: Mapped[str] = mapped_column(String(32), nullable=False, default="monthly")

    price: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(8), nullable=False, default="KZT")

    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), index=True)
    canceled_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    last_payment_id: Mapped[Optional[int]] = mapped_column(ForeignKey("billing_payments.id"))
    next_billing_date: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), index=True)

    auto_renew: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    trial_used: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    grace_period_days: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    company: Mapped[Optional[Any]] = relationship("Company", back_populates="subscriptions")
    payments: Mapped[List[BillingPayment]] = relationship(
        "BillingPayment",
        back_populates="subscription",
        cascade="all, delete-orphan",
        foreign_keys=lambda: [BillingPayment.subscription_id],
    )

    __table_args__ = (
        Index("ix_subscription_company_status", "company_id", "status"),
        CheckConstraint("price >= 0", name="ck_subscription_price_nonneg"),
        CheckConstraint("grace_period_days >= 0", name="ck_subscription_grace_nonneg"),
        {"extend_existing": True},
    )

    @validates("currency")
    def _norm_currency(self, _k: str, v: Optional[str]) -> Optional[str]:
        return _norm_currency_code(v, default="KZT")

    @validates("plan", "status", "billing_cycle")
    def _strip_val(self, _k: str, v: Optional[str]) -> Optional[str]:
        return (v or "").strip() or None

    @property
    def effective_status(self) -> str:
        now = utc_now()
        st = (self.status or "").lower()
        if st == "canceled":
            return "canceled"
        if st == "paused":
            return "paused"
        if st == "trial":
            if self.expires_at and self.expires_at < now and not self.is_within_grace():
                return "expired"
            return "trial"
        if st in {"active", ""}:
            if self.expires_at and self.expires_at < now and not self.is_within_grace():
                return "overdue"
            return "active"
        if st == "overdue":
            return "overdue"
        return "expired"

    def is_active(self) -> bool:
        return self.effective_status in {"active", "trial"}

    def grace_expires_at(self) -> Optional[datetime]:
        if not self.expires_at or self.grace_period_days <= 0:
            return None
        return self.expires_at + timedelta(days=self.grace_period_days)

    def is_within_grace(self) -> bool:
        gx = self.grace_expires_at()
        return gx is not None and utc_now() <= gx

    def cancel(self) -> None:
        self.status = "canceled"
        self.canceled_at = utc_now()
        self.auto_renew = False

    def attach_payment(self, payment: BillingPayment) -> None:
        if payment.company_id != self.company_id:
            raise ValueError("Payment company mismatch with subscription")
        self.last_payment_id = payment.id
        if self.auto_renew and self.next_billing_date:
            self.next_billing_date = self._add_cycle(self.next_billing_date, self.billing_cycle)

    def schedule_next_billing(self, *, from_date: Optional[datetime] = None) -> None:
        base = from_date or self.next_billing_date or utc_now()
        self.next_billing_date = self._add_cycle(base, self.billing_cycle)

    @staticmethod
    def _add_cycle(dt: datetime, cycle: str) -> datetime:
        c = (cycle or "monthly").lower().strip()
        if c in ("day", "daily"):
            return dt + timedelta(days=1)
        if c in ("week", "weekly"):
            return dt + timedelta(weeks=1)
        if c in ("year", "yearly", "annually", "annual"):
            try:
                return dt.replace(year=dt.year + 1)
            except ValueError:
                return dt + timedelta(days=365)
        return dt + timedelta(days=30)

    def bill_if_due(
        self,
        session: Session,
        *,
        tax_amount: Decimal | int | float | None = None,
        invoice_prefix: str = "INV",
        invoice_type: str = "subscription",
        status: str = "unpaid",
        notes: Optional[str] = None,
        internal_notes: Optional[str] = None,
    ) -> Optional["Invoice"]:
        now = utc_now()
        if not self.auto_renew:
            return None
        if self.next_billing_date and self.next_billing_date > now:
            return None

        subtotal = _to_decimal(self.price)
        tax = _to_decimal(tax_amount) if tax_amount is not None else Decimal("0")
        total = subtotal + tax

        number = Invoice.generate_number(session, prefix=invoice_prefix, company_id=self.company_id)

        inv = Invoice(
            order_id=None,
            company_id=self.company_id,
            invoice_number=number,
            invoice_type=invoice_type,
            subtotal=subtotal,
            tax_amount=tax,
            total_amount=total,
            currency=self.currency,
            status=status,
            issue_date=now,
            due_date=self._add_cycle(now, self.billing_cycle),
            notes=notes,
            internal_notes=internal_notes,
            payment_id=None,
        )
        session.add(inv)
        session.flush()

        self.schedule_next_billing(from_date=now)
        session.flush()
        return inv

    async def bill_if_due_async(
        self,
        session: AsyncSession,
        *,
        tax_amount: Decimal | int | float | None = None,
        invoice_prefix: str = "INV",
        invoice_type: str = "subscription",
        status: str = "unpaid",
        notes: Optional[str] = None,
        internal_notes: Optional[str] = None,
    ) -> Optional["Invoice"]:
        now = utc_now()
        if not self.auto_renew:
            return None
        if self.next_billing_date and self.next_billing_date > now:
            return None

        subtotal = _to_decimal(self.price)
        tax = _to_decimal(tax_amount) if tax_amount is not None else Decimal("0")
        total = subtotal + tax

        number = await Invoice.generate_number_async(
            session, prefix=invoice_prefix, company_id=self.company_id
        )

        inv = Invoice(
            order_id=None,
            company_id=self.company_id,
            invoice_number=number,
            invoice_type=invoice_type,
            subtotal=subtotal,
            tax_amount=tax,
            total_amount=total,
            currency=self.currency,
            status=status,
            issue_date=now,
            due_date=self._add_cycle(now, self.billing_cycle),
            notes=notes,
            internal_notes=internal_notes,
            payment_id=None,
        )
        session.add(inv)
        await session.flush()

        self.schedule_next_billing(from_date=now)
        await session.flush()
        return inv

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "company_id": self.company_id,
            "plan": self.plan,
            "status": self.status,
            "billing_cycle": self.billing_cycle,
            "price": str(self.price) if self.price is not None else None,
            "currency": self.currency,
            "started_at": _safe_iso(self.started_at),
            "expires_at": _safe_iso(self.expires_at),
            "canceled_at": _safe_iso(self.canceled_at),
            "last_payment_id": self.last_payment_id,
            "next_billing_date": _safe_iso(self.next_billing_date),
            "auto_renew": self.auto_renew,
            "trial_used": self.trial_used,
            "grace_period_days": self.grace_period_days,
            "effective_status": self.effective_status,
        }


# ======================================================================
# Invoice
# ======================================================================
class Invoice(BaseModel, SoftDeleteMixin):
    __tablename__ = "invoices"
    __allow_unmapped__ = True

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)

    order_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"), nullable=True, index=True
    )
    company_id: Mapped[int] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True
    )

    invoice_number: Mapped[str] = mapped_column(String(64), nullable=False, index=True, unique=True)
    invoice_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)

    subtotal: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    tax_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=0)
    total_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)

    currency: Mapped[str] = mapped_column(String(8), nullable=False, default="KZT")
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)

    issue_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    due_date: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    paid_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    pdf_url: Mapped[Optional[str]] = mapped_column(String(1024))
    pdf_path: Mapped[Optional[str]] = mapped_column(String(512))

    notes: Mapped[Optional[str]] = mapped_column(Text)
    internal_notes: Mapped[Optional[str]] = mapped_column(Text)

    payment_id: Mapped[Optional[int]] = mapped_column(ForeignKey("billing_payments.id"))

    company: Mapped[Optional[Any]] = relationship("Company", backref="invoices")
    order: Mapped[Optional["Order"]] = relationship(
        "Order", backref="invoices", foreign_keys=lambda: [Invoice.order_id]
    )
    payment: Mapped[Optional[BillingPayment]] = relationship("BillingPayment")

    __table_args__ = (
        Index("ix_invoice_company_status", "company_id", "status"),
        Index("ix_invoice_company_created", "company_id", "issue_date"),
        CheckConstraint("subtotal >= 0", name="ck_invoice_subtotal_nonneg"),
        CheckConstraint("tax_amount >= 0", name="ck_invoice_tax_nonneg"),
        CheckConstraint("total_amount >= 0", name="ck_invoice_total_nonneg"),
        CheckConstraint(
            "total_amount >= subtotal + COALESCE(tax_amount, 0)", name="ck_invoice_total_ge_sum"
        ),
        {"extend_existing": True},
    )

    @validates("currency")
    def _norm_currency(self, _k: str, v: Optional[str]) -> Optional[str]:
        return _norm_currency_code(v, default="KZT")

    @validates("invoice_type", "status")
    def _strip_short(self, _k: str, v: Optional[str]) -> Optional[str]:
        return (v or "").strip() or None

    @classmethod
    def generate_number(
        cls,
        session: Session,
        *,
        prefix: str = "INV",
        company_id: Optional[int] = None,
        date: Optional[datetime] = None,
        width: int = 5,
    ) -> str:
        dt = date or utc_now()
        ymd = dt.strftime("%Y%m%d")
        base = f"{(prefix or 'INV').upper()}-{ymd}"
        like_pattern = f"{base}-%"
        q = select(cls.invoice_number).where(cls.invoice_number.like(like_pattern))
        if company_id is not None:
            q = q.where(cls.company_id == company_id)
        existing = {row[0] for row in session.execute(q).all()}
        seq = 1
        while True:
            candidate = f"{base}-{str(seq).zfill(width)}"
            if candidate not in existing:
                return candidate
            seq += 1

    @classmethod
    async def generate_number_async(
        cls,
        session: AsyncSession,
        *,
        prefix: str = "INV",
        company_id: Optional[int] = None,
        date: Optional[datetime] = None,
        width: int = 5,
    ) -> str:
        dt = date or utc_now()
        ymd = dt.strftime("%Y%m%d")
        base = f"{(prefix or 'INV').upper()}-{ymd}"
        like_pattern = f"{base}-%"
        q = select(cls.invoice_number).where(cls.invoice_number.like(like_pattern))
        if company_id is not None:
            q = q.where(cls.company_id == company_id)
        rows = await session.execute(q)
        existing = {row[0] for row in rows.all()}
        seq = 1
        while True:
            candidate = f"{base}-{str(seq).zfill(width)}"
            if candidate not in existing:
                return candidate
            seq += 1

    @classmethod
    def get_last_number(cls, session: Session, *, prefix: str = "INV") -> Optional[str]:
        like_pattern = f"{(prefix or 'INV').upper()}-%"
        row = session.execute(
            select(cls.invoice_number)
            .where(cls.invoice_number.like(like_pattern))
            .order_by(cls.invoice_number.desc())
        ).first()
        return row[0] if row else None

    @classmethod
    async def get_last_number_async(
        cls, session: AsyncSession, *, prefix: str = "INV"
    ) -> Optional[str]:
        like_pattern = f"{(prefix or 'INV').upper()}-%"
        row = await session.execute(
            select(cls.invoice_number)
            .where(cls.invoice_number.like(like_pattern))
            .order_by(cls.invoice_number.desc())
        )
        r = row.first()
        return r[0] if r else None

    def mark_paid(self, when: Optional[datetime] = None) -> None:
        self.status = "paid"
        self.paid_at = when or utc_now()

    def mark_overdue(self) -> None:
        if self.status not in {"paid", "canceled"}:
            self.status = "overdue"

    def auto_update_status(self) -> None:
        if self.status in {"paid", "canceled"}:
            return
        if self.due_date and utc_now() > self.due_date:
            self.status = "overdue"

    def link_pdf(self, url: Optional[str] = None, path: Optional[str] = None) -> None:
        self.pdf_url = (url or "").strip() or None
        self.pdf_path = (path or "").strip() or None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "order_id": self.order_id,
            "company_id": self.company_id,
            "invoice_number": self.invoice_number,
            "invoice_type": self.invoice_type,
            "subtotal": str(self.subtotal) if self.subtotal is not None else None,
            "tax_amount": str(self.tax_amount) if self.tax_amount is not None else None,
            "total_amount": str(self.total_amount) if self.total_amount is not None else None,
            "currency": self.currency,
            "status": self.status,
            "issue_date": _safe_iso(self.issue_date),
            "due_date": _safe_iso(self.due_date),
            "paid_at": _safe_iso(self.paid_at),
            "pdf_url": self.pdf_url,
            "pdf_path": self.pdf_path,
            "notes": self.notes,
            "internal_notes": self.internal_notes,
            "payment_id": self.payment_id,
        }

    @classmethod
    def factory(
        cls,
        *,
        company_id: int,
        total_amount: Decimal | int | float,
        currency: str = "KZT",
        order_id: Optional[int] = None,
        invoice_type: str = "general",
        status: str = "unpaid",
        subtotal: Optional[Decimal | int | float] = None,
        tax_amount: Decimal | int | float = 0,
        issue_date: Optional[datetime] = None,
        due_in_days: int = 7,
        notes: Optional[str] = None,
        internal_notes: Optional[str] = None,
        number: Optional[str] = None,
    ) -> "Invoice":
        sub = (
            _to_decimal(subtotal)
            if subtotal is not None
            else _to_decimal(total_amount) - _to_decimal(tax_amount)
        )
        return cls(
            order_id=order_id,
            company_id=company_id,
            invoice_number=number or "",
            invoice_type=invoice_type,
            subtotal=sub,
            tax_amount=_to_decimal(tax_amount),
            total_amount=_to_decimal(total_amount),
            currency=_norm_currency_code(currency, default="KZT"),
            status=status,
            issue_date=issue_date or utc_now(),
            due_date=(issue_date or utc_now()) + timedelta(days=due_in_days),
            notes=notes,
            internal_notes=internal_notes,
            payment_id=None,
        )

    # unified OperationRow
    def to_operation_row(self) -> Dict[str, Any]:
        return {
            "kind": "invoice",
            "id": self.id,
            "direction": "out",
            "amount": str(self.total_amount),
            "currency": _norm_currency_code(self.currency, default="KZT"),
            "status": self.status,
            "title": f"Счёт {self.invoice_number or ''}".strip(),
            "created_at": _safe_iso(self.issue_date or self.created_at if hasattr(self, "created_at") else None),
            "completed_at": _safe_iso(self.paid_at or self.due_date),
            "links": {"pdf": self.pdf_url} if self.pdf_url else {},
            "is_success": self.status == "paid",
        }


# ======================================================================
# BillingInvoice (расширенный — долги)
# ======================================================================
class BillingInvoice(BaseModel, SoftDeleteMixin):
    __tablename__ = "billing_invoices"
    __allow_unmapped__ = True

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)

    order_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"), nullable=True, unique=True, index=True
    )
    company_id: Mapped[int] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True
    )

    number: Mapped[Optional[str]] = mapped_column(String(64), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="draft", index=True)

    currency: Mapped[str] = mapped_column(String(8), default="KZT", nullable=False)
    subtotal: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0, nullable=False)
    tax_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0, nullable=False)
    discount_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0, nullable=False)
    total_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0, nullable=False)

    # Новые поля для контроля долга/оплат
    total_due: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0, nullable=False)
    paid_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0, nullable=False)

    issued_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), index=True)
    due_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), index=True)
    paid_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), index=True)

    notes: Mapped[Optional[str]] = mapped_column(Text)
    meta: Mapped[Optional[str]] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False, index=True
    )

    order: Mapped[Optional["Order"]] = relationship(
        "Order", back_populates="invoice", foreign_keys=lambda: [BillingInvoice.order_id]
    )

    __table_args__ = (
        CheckConstraint(
            "subtotal >= 0 AND tax_amount >= 0 AND discount_amount >= 0 AND total_amount >= 0",
            name="ck_bi_non_negative",
        ),
        CheckConstraint("total_due >= 0", name="ck_bi_total_due_nonneg"),
        CheckConstraint("paid_amount >= 0", name="ck_bi_paid_amount_nonneg"),
        Index("ix_billing_invoices_status_due", "status", "due_at"),
        Index("ix_billing_invoices_company_status", "company_id", "status"),
        {"extend_existing": True},
    )

    @validates("currency")
    def _norm_currency(self, _k: str, v: Optional[str]) -> Optional[str]:
        return _norm_currency_code(v, default="KZT")

    # ---- бизнес-хелперы по долгу ----
    def remaining_due(self) -> Decimal:
        """Сколько ещё нужно заплатить по этому счёту."""
        total = _to_decimal(self.total_due or self.total_amount)
        paid = _to_decimal(self.paid_amount)
        remain = total - paid
        return max(remain, Decimal("0.00"))

    def apply_payment(self, amount: Decimal | int | float, *, when: Optional[datetime] = None) -> None:
        amt = _to_decimal(amount)
        if amt <= 0:
            return
        self.paid_amount = _to_decimal(self.paid_amount) + amt
        if self.remaining_due() <= Decimal("0.00"):
            self.status = "paid"
            self.paid_at = when or utc_now()

    def mark_paid(self) -> None:
        self.status = "paid"
        self.paid_at = utc_now()
        self.paid_amount = _to_decimal(self.total_due or self.total_amount)

    def mark_overdue(self) -> None:
        if self.status not in {"paid", "canceled"}:
            self.status = "overdue"

    def is_unsettled(self) -> bool:
        return self.status not in {"paid", "canceled"} and self.remaining_due() > Decimal("0")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "order_id": self.order_id,
            "company_id": self.company_id,
            "number": self.number,
            "status": self.status,
            "currency": self.currency,
            "subtotal": str(self.subtotal),
            "tax_amount": str(self.tax_amount),
            "discount_amount": str(self.discount_amount),
            "total_amount": str(self.total_amount),
            "total_due": str(self.total_due),
            "paid_amount": str(self.paid_amount),
            "remaining_due": str(self.remaining_due()),
            "issued_at": _safe_iso(self.issued_at),
            "due_at": _safe_iso(self.due_at),
            "paid_at": _safe_iso(self.paid_at),
            "notes": self.notes,
            "meta": self.meta,
        }

    # unified OperationRow
    def to_operation_row(self) -> Dict[str, Any]:
        return {
            "kind": "billing_invoice",
            "id": self.id,
            "direction": "out",
            "amount": str(self.total_due or self.total_amount),
            "currency": _norm_currency_code(self.currency, default="KZT"),
            "status": self.status,
            "title": f"Счёт {self.number or ''}".strip(),
            "created_at": _safe_iso(self.issued_at or self.created_at),
            "completed_at": _safe_iso(self.paid_at or self.due_at),
            "links": {},
            "is_success": self.status == "paid",
            "remaining_due": str(self.remaining_due()),
        }


# ======================================================================
# WalletBalance
# ======================================================================
class WalletBalance(BaseModel, SoftDeleteMixin):
    __tablename__ = "wallet_balances"
    __allow_unmapped__ = True

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)

    company_id: Mapped[int] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, unique=True, index=True
    )

    balance: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=0)
    currency: Mapped[str] = mapped_column(String(8), nullable=False, default="KZT")
    credit_limit: Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 2), default=0)

    auto_topup_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    auto_topup_threshold: Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 2))
    auto_topup_amount: Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 2))

    # Optimistic locking
    version_id: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    __mapper_args__ = {"version_id_col": version_id}

    company: Mapped[Optional[Any]] = relationship(
        "Company", backref=backref("wallet_balance", uselist=False)
    )
    transactions: Mapped[list[Any]] = relationship(
        "WalletTransaction", back_populates="wallet", cascade="all, delete-orphan"
    )

    __table_args__ = (
        CheckConstraint("balance >= 0", name="ck_wallet_balance_nonneg"),
        CheckConstraint("(credit_limit IS NULL OR credit_limit >= 0)", name="ck_wallet_credit_nonneg"),
        CheckConstraint(
            "(auto_topup_threshold IS NULL OR auto_topup_threshold >= 0)",
            name="ck_wallet_autotopup_threshold_nonneg",
        ),
        CheckConstraint(
            "(auto_topup_amount IS NULL OR auto_topup_amount > 0)",
            name="ck_wallet_autotopup_amount_pos",
        ),
        {"extend_existing": True},
    )

    # -------- хуки/интеграции (НЕ маппим! помечаем ClassVar) --------
    gateway_charge: ClassVar[Any] = None
    gateway_topup: ClassVar[Any] = None
    on_autotopup: ClassVar[Any] = None
    on_low_balance: ClassVar[Any] = None
    on_gateway_error: ClassVar[Any] = None

    # ----------------- пессимистические блокировки -----------------
    @contextmanager
    def lock_for_update(self, session: Session, *, nowait: bool = False, skip_locked: bool = False) -> Iterator[None]:
        stmt = select(WalletBalance).where(WalletBalance.id == self.id).with_for_update(nowait=nowait)
        try:
            if skip_locked and getattr(session.bind.dialect, "name", "") == "postgresql":
                stmt = stmt.with_for_update(skip_locked=True)
        except Exception:
            pass
        session.execute(stmt).all()
        session.refresh(self)
        try:
            yield
        finally:
            ...

    @asynccontextmanager
    async def lock_for_update_async(self, session: AsyncSession, *, nowait: bool = False, skip_locked: bool = False) -> AsyncIterator[None]:
        stmt = select(WalletBalance).where(WalletBalance.id == self.id).with_for_update(nowait=nowait)
        try:
            if skip_locked and getattr(session.bind.dialect, "name", "") == "postgresql":
                stmt = stmt.with_for_update(skip_locked=True)
        except Exception:
            pass
        await session.execute(stmt)
        await session.refresh(self)
        try:
            yield
        finally:
            ...

    @staticmethod
    def _should_retry(exc: Exception) -> bool:
        return isinstance(exc, (StaleDataError, OperationalError, DBAPIError))

    # ----------------- бизнес-логика кошелька -----------------
    def can_spend(self, amount: Decimal) -> bool:
        """Доступно к трате: balance + credit_limit; при этом сам баланс не уходит в минус — дефицит покрываем топапом."""
        available = (self.balance or Decimal("0")) + (self.credit_limit or Decimal("0"))
        return available >= amount

    def _auto_topup_deficit_via_gateway(
        self, *, session: Session, deficit: Decimal, idempotency_key: Optional[str] = None
    ) -> None:
        if deficit <= 0:
            return
        if (self.credit_limit or Decimal("0")) < deficit:
            raise ValueError("Insufficient wallet funds (credit limit exceeded)")
        if not self.gateway_topup:
            raise ValueError("Insufficient wallet funds and no gateway_topup available")
        ok, _ext = False, None
        try:
            ok, _ext = self.gateway_topup(deficit, idempotency_key or "")
        except Exception as e:
            log.warning("gateway_topup(deficit) raised: %s", e)
            if self.on_gateway_error:
                try:
                    self.on_gateway_error(self, "topup", deficit, {"error": str(e)})
                except Exception:
                    pass
            ok = False
        if not ok:
            if self.on_gateway_error:
                try:
                    self.on_gateway_error(self, "topup", deficit, {"error": "gateway_failed"})
                except Exception:
                    pass
            raise ValueError("External gateway topup failed")
        self.credit(
            deficit,
            session=session,
            description="auto_deficit_topup",
            reference_type="gateway_topup",
            reference_id=None,
        )

    async def _auto_topup_deficit_via_gateway_async(
        self,
        *,
        session: AsyncSession,
        deficit: Decimal,
        idempotency_key: Optional[str] = None
    ) -> None:
        if deficit <= 0:
            return
        if (self.credit_limit or Decimal("0")) < deficit:
            raise ValueError("Insufficient wallet funds (credit limit exceeded)")
        if not self.gateway_topup:
            raise ValueError("Insufficient wallet funds and no gateway_topup available")
        ok, _ext = False, None
        try:
            ok, _ext = self.gateway_topup(deficit, idempotency_key or "")
        except Exception as e:
            log.warning("gateway_topup(deficit) raised: %s", e)
            if self.on_gateway_error:
                try:
                    self.on_gateway_error(self, "topup", deficit, {"error": str(e)})
                except Exception:
                    pass
            ok = False
        if not ok:
            if self.on_gateway_error:
                try:
                    self.on_gateway_error(self, "topup", deficit, {"error": "gateway_failed"})
                except Exception:
                    pass
            raise ValueError("External gateway topup failed")
        await self.credit_async(
            session,
            deficit,
            description="auto_deficit_topup",
            reference_type="gateway_topup",
            reference_id=None,
        )

    def credit(
        self,
        amount: Decimal,
        *,
        session: Optional[Session] = None,
        description: str = "credit",
        reference_type: Optional[str] = None,
        reference_id: Optional[int] = None,
    ) -> "WalletTransaction":
        if amount <= 0:
            raise ValueError("Credit amount must be positive")
        before = self.balance or Decimal("0")
        after = before + _to_decimal(amount)
        self.balance = after
        trx = WalletTransaction(
            wallet_id=self.id,
            transaction_type="credit",
            amount=_to_decimal(amount),
            balance_before=before,
            balance_after=after,
            description=description,
            reference_type=reference_type,
            reference_id=reference_id,
        )
        self.transactions.append(trx)
        if session:
            session.flush()
        return trx

    async def credit_async(
        self,
        session: AsyncSession,
        amount: Decimal | int | float,
        *,
        description: str = "credit",
        reference_type: Optional[str] = None,
        reference_id: Optional[int] = None,
    ) -> "WalletTransaction":
        amt = _to_decimal(amount)
        if amt <= 0:
            raise ValueError("Credit amount must be positive")
        before = self.balance or Decimal("0")
        after = before + amt
        self.balance = after
        trx = WalletTransaction(
            wallet_id=self.id,
            transaction_type="credit",
            amount=amt,
            balance_before=before,
            balance_after=after,
            description=description,
            reference_type=reference_type,
            reference_id=reference_id,
        )
        self.transactions.append(trx)
        await session.flush()
        return trx

    def debit(
        self,
        amount: Decimal,
        *,
        session: Optional[Session] = None,
        description: str = "debit",
        reference_type: Optional[str] = None,
        reference_id: Optional[int] = None,
        idempotency_key: Optional[str] = None,
    ) -> "WalletTransaction":
        if amount <= 0:
            raise ValueError("Debit amount must be positive")
        before = self.balance or Decimal("0")
        amt = _to_decimal(amount)
        if before < amt:
            deficit = amt - before
            if session is None:
                raise ValueError("Insufficient wallet funds")
            self._auto_topup_deficit_via_gateway(session=session, deficit=deficit, idempotency_key=idempotency_key)
            before = self.balance or Decimal("0")
        if before < amt:
            raise ValueError("Insufficient wallet balance")
        after = before - amt
        self.balance = after
        trx = WalletTransaction(
            wallet_id=self.id,
            transaction_type="debit",
            amount=amt,
            balance_before=before,
            balance_after=after,
            description=description,
            reference_type=reference_type,
            reference_id=reference_id,
        )
        self.transactions.append(trx)
        if session:
            session.flush()
        return trx

    async def debit_async(
        self,
        session: AsyncSession,
        amount: Decimal | int | float,
        *,
        description: str = "debit",
        reference_type: Optional[str] = None,
        reference_id: Optional[int] = None,
        idempotency_key: Optional[str] = None,
    ) -> "WalletTransaction":
        amt = _to_decimal(amount)
        if amt <= 0:
            raise ValueError("Debit amount must be positive")
        before = self.balance or Decimal("0")
        if before < amt:
            deficit = amt - before
            await self._auto_topup_deficit_via_gateway_async(
                session=session, deficit=deficit, idempotency_key=idempotency_key
            )
            before = self.balance or Decimal("0")
        if before < amt:
            raise ValueError("Insufficient wallet balance")
        after = before - amt
        self.balance = after
        trx = WalletTransaction(
            wallet_id=self.id,
            transaction_type="debit",
            amount=amt,
            balance_before=before,
            balance_after=after,
            description=description,
            reference_type=reference_type,
            reference_id=reference_id,
        )
        self.transactions.append(trx)
        await session.flush()
        return trx

    def debit_safe(
        self,
        session: Session,
        amount: Decimal | int | float,
        *,
        description: str = "debit",
        reference_type: Optional[str] = None,
        reference_id: Optional[int] = None,
        retries: int = 3,
        nowait: bool = False,
        skip_locked: bool = False,
        idempotency_key: Optional[str] = None,
    ) -> "WalletTransaction":
        amt = _to_decimal(amount)
        attempt = 0
        last_exc: Optional[Exception] = None
        while attempt <= retries:
            attempt += 1
            try:
                with self.lock_for_update(session, nowait=nowait, skip_locked=skip_locked):
                    before = self.balance or Decimal("0")
                    if before < amt:
                        deficit = amt - before
                        self._auto_topup_deficit_via_gateway(
                            session=session, deficit=deficit, idempotency_key=idempotency_key
                        )
                        before = self.balance or Decimal("0")
                    if before < amt:
                        raise ValueError("Insufficient wallet balance")
                    after = before - amt
                    self.balance = after
                    trx = WalletTransaction(
                        wallet_id=self.id,
                        transaction_type="debit",
                        amount=amt,
                        balance_before=before,
                        balance_after=after,
                        description=description,
                        reference_type=reference_type,
                        reference_id=reference_id,
                    )
                    self.transactions.append(trx)
                    session.flush()
                    return trx
            except Exception as e:
                if self._should_retry(e) and attempt <= retries:
                    log.warning("debit_safe retry %s/%s due to %s", attempt, retries, repr(e))
                    last_exc = e
                    continue
                raise
        if last_exc:
            raise last_exc
        raise RuntimeError("debit_safe failed unexpectedly")

    async def debit_safe_async(
        self,
        session: AsyncSession,
        amount: Decimal | int | float,
        *,
        description: str = "debit",
        reference_type: Optional[str] = None,
        reference_id: Optional[int] = None,
        retries: int = 3,
        nowait: bool = False,
        skip_locked: bool = False,
        idempotency_key: Optional[str] = None,
    ) -> "WalletTransaction":
        amt = _to_decimal(amount)
        attempt = 0
        last_exc: Optional[Exception] = None
        while attempt <= retries:
            attempt += 1
            try:
                async with self.lock_for_update_async(session, nowait=nowait, skip_locked=skip_locked):
                    before = self.balance or Decimal("0")
                    if before < amt:
                        deficit = amt - before
                        await self._auto_topup_deficit_via_gateway_async(
                            session=session, deficit=deficit, idempotency_key=idempotency_key
                        )
                        before = self.balance or Decimal("0")
                    if before < amt:
                        raise ValueError("Insufficient wallet balance")
                    after = before - amt
                    self.balance = after
                    trx = WalletTransaction(
                        wallet_id=self.id,
                        transaction_type="debit",
                        amount=amt,
                        balance_before=before,
                        balance_after=after,
                        description=description,
                        reference_type=reference_type,
                        reference_id=reference_id,
                    )
                    self.transactions.append(trx)
                    await session.flush()
                    return trx
            except Exception as e:
                if self._should_retry(e) and attempt <= retries:
                    log.warning("debit_safe_async retry %s/%s due to %s", attempt, retries, repr(e))
                    last_exc = e
                    continue
                raise
        if last_exc:
            raise last_exc

    def maybe_autotopup(
        self,
        session: Session,
        *,
        description: str = "auto_topup",
        reference_type: Optional[str] = "autotopup",
        reference_id: Optional[int] = None,
    ) -> Optional["WalletTransaction"]:
        if not self.auto_topup_enabled:
            return None
        if self.auto_topup_threshold is None or self.auto_topup_amount is None:
            return None
        if (self.balance or Decimal("0")) <= (self.auto_topup_threshold or Decimal("0")):
            amount = _to_decimal(self.auto_topup_amount)
            trx = self.credit(
                amount,
                session=session,
                description=description,
                reference_type=reference_type,
                reference_id=reference_id,
            )
            if self.on_autotopup:
                try:
                    self.on_autotopup(self, trx)
                except Exception:
                    pass
            return trx
        return None

    def maybe_notify_low_balance(self, *, threshold: Optional[Decimal] = None) -> bool:
        thr = _to_decimal(threshold) if threshold is not None else (self.auto_topup_threshold or Decimal("0"))
        if (self.balance or Decimal("0")) <= thr:
            if self.on_low_balance:
                try:
                    self.on_low_balance(self)
                except Exception:
                    pass
            return True
        return False

    def charge_via_gateway(
        self,
        amount: Decimal | int | float,
        *,
        session: Session,
        idempotency_key: Optional[str] = None,
        description: str = "gateway_charge",
    ) -> "WalletTransaction":
        amt = _to_decimal(amount)
        if amt <= 0:
            raise ValueError("Amount must be positive")
        if self.gateway_charge:
            ok, _ext_tx = False, None
            try:
                ok, _ext_tx = self.gateway_charge(amt, idempotency_key or "")
            except Exception as e:
                log.warning("gateway_charge raised exception: %s", e)
                if self.on_gateway_error:
                    try:
                        self.on_gateway_error(self, "charge", amt, {"error": str(e)})
                    except Exception:
                        pass
                ok = False
            if not ok:
                log.warning("gateway_charge failed (amount=%s, key=%s)", amt, idempotency_key)
                if self.on_gateway_error:
                    try:
                        self.on_gateway_error(self, "charge", amt, {"error": "gateway_failed"})
                    except Exception:
                        pass
                raise ValueError("External gateway charge failed")
            return self.debit_safe(
                session,
                amt,
                description=description,
                reference_type="gateway_charge",
                reference_id=None,
            )
        return self.debit_safe(session, amt, description=description, reference_type="local", reference_id=None)

    def topup_via_gateway(
        self,
        amount: Decimal | int | float,
        *,
        session: Session,
        idempotency_key: Optional[str] = None,
        description: str = "gateway_topup",
    ) -> "WalletTransaction":
        amt = _to_decimal(amount)
        if amt <= 0:
            raise ValueError("Amount must be positive")
        if self.gateway_topup:
            ok, _ext_tx = False, None
            try:
                ok, _ext_tx = self.gateway_topup(amt, idempotency_key or "")
            except Exception as e:
                log.warning("gateway_topup raised exception: %s", e)
                if self.on_gateway_error:
                    try:
                        self.on_gateway_error(self, "topup", amt, {"error": str(e)})
                    except Exception:
                        pass
                ok = False
            if not ok:
                log.warning("gateway_topup failed (amount=%s, key=%s)", amt, idempotency_key)
                if self.on_gateway_error:
                    try:
                        self.on_gateway_error(self, "topup", amt, {"error": "gateway_failed"})
                    except Exception:
                        pass
                raise ValueError("External gateway topup failed")
            return self.credit(amt, session=session, description=description, reference_type="gateway_topup", reference_id=None)
        return self.credit(amt, session=session, description=description, reference_type="local", reference_id=None)

    def _ensure_currency_compat(self, *, expected_currency: Optional[str]) -> None:
        if expected_currency and (self.currency or "").upper() != (expected_currency or "").upper():
            raise ValueError(f"Currency mismatch: wallet={self.currency} expected={expected_currency}")

    def settle_amount_with_wallet(
        self,
        session: Session,
        *,
        company: "Company",
        amount: Decimal | int | float,
        currency: str = "KZT",
        leave_min_positive: bool = True,
        min_positive_balance: Decimal | int | float = Decimal("0.01"),
        allow_zero_balance: bool = True,
        invoice_type: str = "subscription",
        invoice_prefix: str = "INV",
        invoice_status: str = "unpaid",
        invoice_notes: Optional[str] = None,
        invoice_internal_notes: Optional[str] = None,
        use_locking: bool = True,
        retries: int = 3,
    ) -> Dict[str, Any]:
        req = _to_decimal(amount)
        if req <= 0:
            raise ValueError("Amount must be positive")

        self._ensure_currency_compat(expected_currency=currency)

        def _compute_spend(current: Decimal) -> Tuple[Decimal, Decimal]:
            min_keep = _to_decimal(min_positive_balance) if leave_min_positive else Decimal("0")
            if allow_zero_balance and min_keep > 0:
                min_keep = min(min_keep, Decimal("0.00"))
            effective_min = Decimal("0.00") if allow_zero_balance else max(min_keep, Decimal("0.01"))
            max_spend_local = max(Decimal("0"), current - effective_min)
            wallet_spend_local = min(req, max_spend_local)
            missing_local = req - wallet_spend_local
            return wallet_spend_local, missing_local

        attempt = 0
        last_exc: Optional[Exception] = None

        while True:
            try:
                if use_locking:
                    with self.lock_for_update(session):
                        current = self.balance or Decimal("0")
                        wallet_spend, missing = _compute_spend(current)
                        inv: Optional[Invoice] = None

                        if wallet_spend > 0:
                            self.debit_safe(session, wallet_spend, description="settle", reference_type="settle")

                        if missing > 0:
                            number = Invoice.generate_number(session, prefix=invoice_prefix, company_id=company.id)
                            inv = Invoice(
                                order_id=None,
                                company_id=company.id,
                                invoice_number=number,
                                invoice_type=invoice_type,
                                subtotal=missing,
                                tax_amount=Decimal("0"),
                                total_amount=missing,
                                currency=currency,
                                status=invoice_status,
                                issue_date=utc_now(),
                                due_date=utc_now() + timedelta(days=7),
                                notes=invoice_notes,
                                internal_notes=invoice_internal_notes,
                                payment_id=None,
                            )
                            session.add(inv)
                            session.flush()
                            _emit_audit_safe(session=session, company_id=company.id, action="wallet.settle_invoice_created", meta={"missing": str(missing), "invoice_number": inv.invoice_number})
                        else:
                            _emit_audit_safe(session=session, company_id=company.id, action="wallet.settle_covered_by_wallet", meta={"spent": str(wallet_spend)})

                        return {"requested": req, "wallet_spent": wallet_spend, "missing": missing, "invoice": inv}
                else:
                    current = self.balance or Decimal("0")
                    wallet_spend, missing = _compute_spend(current)
                    inv: Optional[Invoice] = None

                    if wallet_spend > 0:
                        self.debit(wallet_spend, session=session, description="settle", reference_type="settle")

                    if missing > 0:
                        number = Invoice.generate_number(session, prefix=invoice_prefix, company_id=company.id)
                        inv = Invoice(
                            order_id=None,
                            company_id=company.id,
                            invoice_number=number,
                            invoice_type=invoice_type,
                            subtotal=missing,
                            tax_amount=Decimal("0"),
                            total_amount=missing,
                            currency=currency,
                            status=invoice_status,
                            issue_date=utc_now(),
                            due_date=utc_now() + timedelta(days=7),
                            notes=invoice_notes,
                            internal_notes=invoice_internal_notes,
                            payment_id=None,
                        )
                        session.add(inv)
                        session.flush()
                        _emit_audit_safe(session=session, company_id=company.id, action="wallet.settle_invoice_created", meta={"missing": str(missing), "invoice_number": inv.invoice_number})
                    else:
                        _emit_audit_safe(session=session, company_id=company.id, action="wallet.settle_covered_by_wallet", meta={"spent": str(wallet_spend)})

                    return {"requested": req, "wallet_spent": wallet_spend, "missing": missing, "invoice": inv}
            except Exception as e:
                if use_locking and self._should_retry(e) and attempt < retries:
                    attempt += 1
                    log.warning("settle_amount_with_wallet retry %s/%s due to %s", attempt, retries, repr(e))
                    last_exc = e
                    continue
                raise

    async def settle_amount_with_wallet_async(
        self,
        session: AsyncSession,
        *,
        company: "Company",
        amount: Decimal | int | float,
        currency: str = "KZT",
        leave_min_positive: bool = True,
        min_positive_balance: Decimal | int | float = Decimal("0.01"),
        allow_zero_balance: bool = True,
        invoice_type: str = "subscription",
        invoice_prefix: str = "INV",
        invoice_status: str = "unpaid",
        invoice_notes: Optional[str] = None,
        invoice_internal_notes: Optional[str] = None,
        use_locking: bool = True,
        retries: int = 3,
    ) -> Dict[str, Any]:
        req = _to_decimal(amount)
        if req <= 0:
            raise ValueError("Amount must be positive")
        self._ensure_currency_compat(expected_currency=currency)

        def _compute_spend(current: Decimal) -> Tuple[Decimal, Decimal]:
            min_keep = _to_decimal(min_positive_balance) if leave_min_positive else Decimal("0")
            if allow_zero_balance and min_keep > 0:
                min_keep = min(min_keep, Decimal("0.00"))
            effective_min = Decimal("0.00") if allow_zero_balance else max(min_keep, Decimal("0.01"))
            max_spend_local = max(Decimal("0"), current - effective_min)
            wallet_spend_local = min(req, max_spend_local)
            missing_local = req - wallet_spend_local
            return wallet_spend_local, missing_local

        attempt = 0
        last_exc: Optional[Exception] = None

        while True:
            try:
                if use_locking:
                    async with self.lock_for_update_async(session):
                        current = self.balance or Decimal("0")
                        wallet_spend, missing = _compute_spend(current)
                        inv: Optional[Invoice] = None

                        if wallet_spend > 0:
                            await self.debit_safe_async(session, wallet_spend, description="settle", reference_type="settle")

                        if missing > 0:
                            number = await Invoice.generate_number_async(session, prefix=invoice_prefix, company_id=company.id)
                            inv = Invoice(
                                order_id=None,
                                company_id=company.id,
                                invoice_number=number,
                                invoice_type=invoice_type,
                                subtotal=missing,
                                tax_amount=Decimal("0"),
                                total_amount=missing,
                                currency=currency,
                                status=invoice_status,
                                issue_date=utc_now(),
                                due_date=utc_now() + timedelta(days=7),
                                notes=invoice_notes,
                                internal_notes=invoice_internal_notes,
                                payment_id=None,
                            )
                            session.add(inv)
                            await session.flush()
                            await _emit_audit_safe_async(session=session, company_id=company.id, action="wallet.settle_invoice_created", meta={"missing": str(missing), "invoice_number": inv.invoice_number})
                        else:
                            await _emit_audit_safe_async(session=session, company_id=company.id, action="wallet.settle_covered_by_wallet", meta={"spent": str(wallet_spend)})

                        return {"requested": req, "wallet_spent": wallet_spend, "missing": missing, "invoice": inv}
                else:
                    current = self.balance or Decimal("0")
                    wallet_spend, missing = _compute_spend(current)
                    inv: Optional[Invoice] = None

                    if wallet_spend > 0:
                        before = self.balance or Decimal("0")
                        after = before - wallet_spend
                        if after < 0:
                            raise ValueError("Insufficient wallet balance")
                        self.balance = after
                        trx = WalletTransaction(
                            wallet_id=self.id,
                            transaction_type="debit",
                            amount=wallet_spend,
                            balance_before=before,
                            balance_after=after,
                            description="settle",
                            reference_type="settle",
                            reference_id=None,
                        )
                        self.transactions.append(trx)
                        await session.flush()

                    if missing > 0:
                        number = await Invoice.generate_number_async(session, prefix=invoice_prefix, company_id=company.id)
                        inv = Invoice(
                            order_id=None,
                            company_id=company.id,
                            invoice_number=number,
                            invoice_type=invoice_type,
                            subtotal=missing,
                            tax_amount=Decimal("0"),
                            total_amount=missing,
                            currency=currency,
                            status=invoice_status,
                            issue_date=utc_now(),
                            due_date=utc_now() + timedelta(days=7),
                            notes=invoice_notes,
                            internal_notes=invoice_internal_notes,
                            payment_id=None,
                        )
                        session.add(inv)
                        await session.flush()
                        await _emit_audit_safe_async(session=session, company_id=company.id, action="wallet.settle_invoice_created", meta={"missing": str(missing), "invoice_number": inv.invoice_number})
                    else:
                        await _emit_audit_safe_async(session=session, company_id=company.id, action="wallet.settle_covered_by_wallet", meta={"spent": str(wallet_spend)})

                    return {"requested": req, "wallet_spent": wallet_spend, "missing": missing, "invoice": inv}
            except Exception as e:
                if use_locking and self._should_retry(e) and attempt < retries:
                    attempt += 1
                    log.warning("settle_amount_with_wallet_async retry %s/%s due to %s", attempt, retries, repr(e))
                    last_exc = e
                    continue
                raise

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "company_id": self.company_id,
            "balance": str(self.balance) if self.balance is not None else None,
            "currency": self.currency,
            "credit_limit": str(self.credit_limit) if self.credit_limit is not None else None,
            "auto_topup_enabled": self.auto_topup_enabled,
            "auto_topup_threshold": str(self.auto_topup_threshold) if self.auto_topup_threshold is not None else None,
            "auto_topup_amount": str(self.auto_topup_amount) if self.auto_topup_amount is not None else None,
            "version_id": self.version_id,
        }

    @classmethod
    def factory(
        cls,
        company_id: int,
        *,
        balance: Decimal | int | float = 0,
        currency: str = "KZT",
        credit_limit: Decimal | int | float = 0,
        auto_topup_enabled: bool = False,
        auto_topup_threshold: Decimal | int | float | None = None,
        auto_topup_amount: Decimal | int | float | None = None,
    ) -> "WalletBalance":
        return cls(
            company_id=company_id,
            balance=_to_decimal(balance),
            currency=_norm_currency_code(currency, default="KZT"),
            credit_limit=_to_decimal(credit_limit),
            auto_topup_enabled=auto_topup_enabled,
            auto_topup_threshold=(None if auto_topup_threshold is None else _to_decimal(auto_topup_threshold)),
            auto_topup_amount=(None if auto_topup_amount is None else _to_decimal(auto_topup_amount)),
        )

    @classmethod
    def get_for_company(
        cls,
        session: Session,
        company_id: int,
        *,
        create_if_missing: bool = False,
        currency: str = "KZT",
    ) -> "WalletBalance":
        wb = session.execute(select(cls).where(cls.company_id == company_id).limit(1)).scalar_one_or_none()
        if wb:
            return wb
        if not create_if_missing:
            raise LookupError("WalletBalance not found for company")
        wb = cls(company_id=company_id, balance=Decimal("0"), currency=_norm_currency_code(currency, default="KZT"))
        session.add(wb)
        session.flush()
        return wb

    @classmethod
    async def get_for_company_async(
        cls,
        session: AsyncSession,
        company_id: int,
        *,
        create_if_missing: bool = False,
        currency: str = "KZT",
    ) -> "WalletBalance":
        row = await session.execute(select(cls).where(cls.company_id == company_id).limit(1))
        wb = row.scalar_one_or_none()
        if wb:
            return wb
        if not create_if_missing:
            raise LookupError("WalletBalance not found for company")
        wb = cls(company_id=company_id, balance=Decimal("0"), currency=_norm_currency_code(currency, default="KZT"))
        session.add(wb)
        await session.flush()
        return wb


# ======================================================================
# WalletTransaction
# ======================================================================
class WalletTransaction(BaseModel, SoftDeleteMixin):
    __tablename__ = "wallet_transactions"
    __allow_unmapped__ = True

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)

    wallet_id: Mapped[int] = mapped_column(
        ForeignKey("wallet_balances.id", ondelete="CASCADE"), nullable=False, index=True
    )

    transaction_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)  # credit|debit|adjustment
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)

    balance_before: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    balance_after: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)

    reference_type: Mapped[Optional[str]] = mapped_column(String(32))
    reference_id: Mapped[Optional[int]] = mapped_column(Integer, index=True)

    description: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    extra_data: Mapped[Optional[str]] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False, index=True)

    wallet: Mapped[Any] = relationship("WalletBalance", back_populates="transactions")

    __table_args__ = (
        Index("ix_wallet_transaction_wallet_type", "wallet_id", "transaction_type"),
        CheckConstraint("amount >= 0", name="ck_wallet_trx_amount_nonneg"),
        CheckConstraint("balance_before >= 0", name="ck_wallet_trx_before_nonneg"),
        CheckConstraint("balance_after >= 0", name="ck_wallet_trx_after_nonneg"),
        {"extend_existing": True},
    )

    @validates("transaction_type")
    def _validate_type(self, _k: str, v: str) -> str:
        allowed = {"credit", "debit", "adjustment"}
        vv = (v or "").strip().lower()
        if vv not in allowed:
            raise ValueError(f"Invalid transaction_type: {vv}")
        return vv

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "wallet_id": self.wallet_id,
            "transaction_type": self.transaction_type,
            "amount": str(self.amount) if self.amount is not None else None,
            "balance_before": str(self.balance_before) if self.balance_before is not None else None,
            "balance_after": str(self.balance_after) if self.balance_after is not None else None,
            "reference_type": self.reference_type,
            "reference_id": self.reference_id,
            "description": self.description,
            "extra_data": self.extra_data,
        }

    # unified OperationRow
    def to_operation_row(self) -> Dict[str, Any]:
        direction = "in" if self.transaction_type == "credit" else "out"
        return {
            "kind": "wallet_txn",
            "id": self.id,
            "direction": direction,
            "amount": str(self.amount),
            "currency": self.wallet.currency if self.wallet else "KZT",
            "status": "done",
            "title": f"Кошелёк: {self.transaction_type}",
            "created_at": _safe_iso(self.created_at),
            "completed_at": _safe_iso(self.created_at),
            "links": {},
            "is_success": True,
        }


# ---------------- audit helpers ----------------
def _emit_audit_safe(*, session: Session, company_id: int, action: str, meta: Optional[Dict[str, Any]] = None) -> None:
    try:
        from app.models.audit import ExternalAuditEvent  # type: ignore
    except Exception:
        return
    evt = ExternalAuditEvent(company_id=company_id, action=action, metadata_json=_json_dumps(meta), created_at=utc_now())
    session.add(evt)


async def _emit_audit_safe_async(*, session: AsyncSession, company_id: int, action: str, meta: Optional[Dict[str, Any]] = None) -> None:
    try:
        from app.models.audit import ExternalAuditEvent  # type: ignore
    except Exception:
        return
    evt = ExternalAuditEvent(company_id=company_id, action=action, metadata_json=_json_dumps(meta), created_at=utc_now())
    session.add(evt)
    await session.flush()


# ---------------- unified operations feed (sync/async) ----------------
OperationKind = Literal["payment", "invoice", "billing_invoice", "wallet_txn"]


def _within_range(dt: Optional[datetime], df: Optional[datetime], dtmax: Optional[datetime]) -> bool:
    if not dt:
        return True
    if df and dt < df:
        return False
    if dtmax and dt >= dtmax:
        return False
    return True


def operations_feed_sync(
    session: Session,
    *,
    company_id: int,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    kinds: Optional[Iterable[OperationKind]] = None,
    limit: int = 500,
) -> List[Dict[str, Any]]:
    """
    Унифицированная лента денежных операций по компании.
    Собирает из: BillingPayment, Invoice, BillingInvoice, WalletTransaction.
    Сортировка по дате создания/завершения у соответствующих сущностей (desc).
    """
    kinds_set = set(kinds or {"payment", "invoice", "billing_invoice", "wallet_txn"})

    out: List[Tuple[datetime, Dict[str, Any]]] = []

    if "payment" in kinds_set:
        rows = session.execute(
            select(BillingPayment).where(BillingPayment.company_id == company_id)
            .order_by(BillingPayment.created_at.desc())
            .limit(limit)
        ).scalars().all()
        for p in rows:
            if _within_range(p.created_at, date_from, date_to):
                out.append((p.created_at or utc_now(), p.to_operation_row()))

    if "invoice" in kinds_set:
        rows = session.execute(
            select(Invoice).where(Invoice.company_id == company_id)
            .order_by(Invoice.issue_date.desc())
            .limit(limit)
        ).scalars().all()
        for inv in rows:
            base_dt = inv.issue_date or getattr(inv, "created_at", None) or utc_now()
            if _within_range(base_dt, date_from, date_to):
                out.append((base_dt, inv.to_operation_row()))

    if "billing_invoice" in kinds_set:
        rows = session.execute(
            select(BillingInvoice).where(BillingInvoice.company_id == company_id)
            .order_by(BillingInvoice.issued_at.desc().nullslast())
            .limit(limit)
        ).scalars().all()
        for bi in rows:
            base_dt = bi.issued_at or bi.created_at
            if _within_range(base_dt, date_from, date_to):
                out.append((base_dt, bi.to_operation_row()))

    if "wallet_txn" in kinds_set:
        # получаем wallet id
        w = session.execute(select(WalletBalance).where(WalletBalance.company_id == company_id)).scalar_one_or_none()
        if w:
            txs = session.execute(
                select(WalletTransaction)
                .where(WalletTransaction.wallet_id == w.id)
                .order_by(WalletTransaction.created_at.desc())
                .limit(limit)
            ).scalars().all()
            for t in txs:
                if _within_range(t.created_at, date_from, date_to):
                    out.append((t.created_at or utc_now(), t.to_operation_row()))

    out.sort(key=lambda x: x[0], reverse=True)
    return [row for _, row in out[:limit]]


async def operations_feed_async(
    session: AsyncSession,
    *,
    company_id: int,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    kinds: Optional[Iterable[OperationKind]] = None,
    limit: int = 500,
) -> List[Dict[str, Any]]:
    kinds_set = set(kinds or {"payment", "invoice", "billing_invoice", "wallet_txn"})
    out: List[Tuple[datetime, Dict[str, Any]]] = []

    if "payment" in kinds_set:
        rows = await session.execute(
            select(BillingPayment).where(BillingPayment.company_id == company_id)
            .order_by(BillingPayment.created_at.desc())
            .limit(limit)
        )
        for p in rows.scalars().all():
            if _within_range(p.created_at, date_from, date_to):
                out.append((p.created_at or utc_now(), p.to_operation_row()))

    if "invoice" in kinds_set:
        rows = await session.execute(
            select(Invoice).where(Invoice.company_id == company_id)
            .order_by(Invoice.issue_date.desc())
            .limit(limit)
        )
        for inv in rows.scalars().all():
            base_dt = inv.issue_date or getattr(inv, "created_at", None) or utc_now()
            if _within_range(base_dt, date_from, date_to):
                out.append((base_dt, inv.to_operation_row()))

    if "billing_invoice" in kinds_set:
        rows = await session.execute(
            select(BillingInvoice).where(BillingInvoice.company_id == company_id)
            .order_by(BillingInvoice.issued_at.desc().nullslast())
            .limit(limit)
        )
        for bi in rows.scalars().all():
            base_dt = bi.issued_at or bi.created_at
            if _within_range(base_dt, date_from, date_to):
                out.append((base_dt, bi.to_operation_row()))

    if "wallet_txn" in kinds_set:
        wrow = await session.execute(select(WalletBalance).where(WalletBalance.company_id == company_id))
        w = wrow.scalar_one_or_none()
        if w:
            txs = await session.execute(
                select(WalletTransaction)
                .where(WalletTransaction.wallet_id == w.id)
                .order_by(WalletTransaction.created_at.desc())
                .limit(limit)
            )
            for t in txs.scalars().all():
                if _within_range(t.created_at, date_from, date_to):
                    out.append((t.created_at or utc_now(), t.to_operation_row()))

    out.sort(key=lambda x: x[0], reverse=True)
    return [row for _, row in out[:limit]]


__all__ = [
    "BillingPayment",
    "Subscription",
    "Invoice",
    "BillingInvoice",
    "WalletBalance",
    "WalletTransaction",
    "operations_feed_sync",
    "operations_feed_async",
    "utc_now",
    "to_astana",
    "ASTANA_TZ",
]
