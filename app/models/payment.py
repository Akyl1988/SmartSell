"""
Payments & Refunds domain (PostgreSQL first, cross-dialect safe).

Сохраняем весь функционал: outbox, SIEM, аналитики, mixed capture, advisory locks.
Дополнительно:
- Кросс-диалектные алиасы типов (чтобы тесты на SQLite не падали по DDL).
- Безопасная однонаправленная связь Payment -> Order без обязательного back_populates,
  чтобы не ломаться, если Order.payments ссылается не на Payment.
- ✅ Исправлено: добавлен симметричный маппинг Payment.customer (back_populates="payments"),
  чтобы не падало с ошибкой "Mapper 'Payment' has no property 'customer'".
"""

from __future__ import annotations

import enum
import json
import logging
import os
import uuid
from collections.abc import Iterable, Sequence
from contextlib import asynccontextmanager, contextmanager
from datetime import UTC, datetime, timedelta
from decimal import ROUND_DOWN, Decimal
from typing import Any, Optional

from sqlalchemy import Boolean, CheckConstraint, Column, DateTime
from sqlalchemy import Enum as SQLEnum
from sqlalchemy import (
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    and_,
    event,
    func,
    or_,
    select,
    text,
)

# ---- Dialect-safe type aliases ------------------------------------------------
# На Postgres используем нативные типы; для других диалектов подменяем на безопасные аналоги
try:
    from sqlalchemy.dialects.postgresql import INET as _PG_INET

    INET_TYPE = _PG_INET
except Exception:  # pragma: no cover
    INET_TYPE = String(64)  # IPv4/IPv6 хватает 64 символов с запасом

try:
    from sqlalchemy.dialects.postgresql import JSONB as _PG_JSONB

    JSONB_TYPE = _PG_JSONB
except Exception:  # pragma: no cover
    from sqlalchemy import JSON as _GENERIC_JSON

    JSONB_TYPE = _GENERIC_JSON  # SQLite/others

try:
    from sqlalchemy.dialects.postgresql import UUID as _PG_UUID

    PG_UUID_TYPE = _PG_UUID
except Exception:  # pragma: no cover
    PG_UUID_TYPE = String(36)  # универсально хранить как текст UUID

from sqlalchemy.orm import Session, relationship

# NEW: async support
try:  # SQLAlchemy async is optional for some deployments
    from sqlalchemy.ext.asyncio import AsyncSession
except Exception:  # pragma: no cover
    AsyncSession = None  # type: ignore

from app.models.base import Base

logger = logging.getLogger("app.audit")


# =========================
# ENUMS
# =========================


class PaymentStatus(str, enum.Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    SUCCESS = "success"
    FAILED = "failed"
    CANCELLED = "cancelled"
    REFUNDED = "refunded"
    PARTIALLY_REFUNDED = "partially_refunded"


class PaymentMethod(str, enum.Enum):
    CARD = "card"
    CASH = "cash"
    BANK_TRANSFER = "bank_transfer"
    WALLET = "wallet"
    QR_CODE = "qr_code"


class PaymentProvider(str, enum.Enum):
    TIPTOP = "tiptop"
    KASPI = "kaspi"
    PAYBOX = "paybox"
    MANUAL = "manual"


class OutboxStatus(str, enum.Enum):
    PENDING = "pending"
    SENT = "sent"
    FAILED = "failed"


class ReconciliationStatus(str, enum.Enum):
    MATCHED = "matched"
    MISSING = "missing"  # в стейтменте есть, у нас нет
    MISMATCH = "mismatch"  # есть, но сумма/валюта/статус не сходится


# =========================
# INTEGRATION OUTBOX
# =========================


class IntegrationOutbox(Base):
    __tablename__ = "integration_outbox"

    id = Column(Integer, primary_key=True, index=True)
    created_at = Column(DateTime(False), nullable=False, server_default=func.now(), index=True)
    updated_at = Column(
        DateTime(False), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    aggregate_type = Column(
        String(64), nullable=False, index=True
    )  # "payment" | "payment_refund" | ...
    aggregate_id = Column(Integer, nullable=False, index=True)

    event_type = Column(String(64), nullable=False, index=True)  # e.g., "payment.succeeded"
    payload = Column(JSONB_TYPE, nullable=False)

    status = Column(String(16), nullable=False, default=OutboxStatus.PENDING.value, index=True)
    attempts = Column(Integer, nullable=False, default=0)
    next_attempt_at = Column(DateTime(False), nullable=True)
    last_error = Column(Text, nullable=True)

    __table_args__ = (
        CheckConstraint("attempts >= 0", name="ck_outbox_attempts_nonneg"),
        Index("ix_outbox_status_due", "status", "next_attempt_at"),
    )

    def mark_sent(self):
        self.status = OutboxStatus.SENT.value
        self.updated_at = datetime.now(UTC).replace(tzinfo=None)
        self.next_attempt_at = None
        self.last_error = None

    def mark_failed(self, err: str, retry_in_seconds: int = 60):
        self.status = OutboxStatus.FAILED.value
        self.attempts = int(self.attempts or 0) + 1
        self.last_error = (err or "")[:2000]
        self.updated_at = datetime.now(UTC).replace(tzinfo=None)
        self.next_attempt_at = self.updated_at + timedelta(seconds=retry_in_seconds)

    # --- Batch accessors (sync) ---
    @staticmethod
    def batch_fetch(
        session: Session,
        *,
        status: OutboxStatus = OutboxStatus.PENDING,
        aggregate_type: Optional[str] = None,
        limit: int = 1000,
        due_only: bool = False,
    ) -> list[IntegrationOutbox]:
        q = select(IntegrationOutbox).where(IntegrationOutbox.status == status.value)
        if aggregate_type:
            q = q.where(IntegrationOutbox.aggregate_type == aggregate_type)
        if due_only:
            q = q.where(
                or_(
                    IntegrationOutbox.next_attempt_at.is_(None),
                    IntegrationOutbox.next_attempt_at <= func.now(),
                )
            )
        q = q.order_by(IntegrationOutbox.created_at.asc()).limit(limit)
        return list(session.execute(q).scalars().all())

    # --- Batch accessors (async) ---
    @staticmethod
    async def batch_fetch_async(
        session: AsyncSession,
        *,
        status: OutboxStatus = OutboxStatus.PENDING,
        aggregate_type: Optional[str] = None,
        limit: int = 1000,
        due_only: bool = False,
    ) -> list[IntegrationOutbox]:
        q = select(IntegrationOutbox).where(IntegrationOutbox.status == status.value)
        if aggregate_type:
            q = q.where(IntegrationOutbox.aggregate_type == aggregate_type)
        if due_only:
            q = q.where(
                or_(
                    IntegrationOutbox.next_attempt_at.is_(None),
                    IntegrationOutbox.next_attempt_at <= func.now(),
                )
            )
        q = q.order_by(IntegrationOutbox.created_at.asc()).limit(limit)
        res = await session.execute(q)
        return list(res.scalars().all())


# =========================
# PAYMENTS
# =========================


class Payment(Base):
    __tablename__ = "payments"

    id = Column(Integer, primary_key=True, index=True)
    uuid = Column(
        PG_UUID_TYPE, unique=True, nullable=False, default=lambda: str(uuid.uuid4()), index=True
    )
    version = Column(Integer, nullable=False, default=1)

    created_at = Column(DateTime(False), nullable=False, server_default=func.now(), index=True)
    updated_at = Column(
        DateTime(False), nullable=False, server_default=func.now(), onupdate=func.now(), index=True
    )

    order_id = Column(ForeignKey("orders.id", ondelete="CASCADE"), nullable=False, index=True)

    # Необязательная ссылка на клиента (для точной аналитики по клиенту)
    customer_id = Column(ForeignKey("customers.id", ondelete="SET NULL"), nullable=True, index=True)

    payment_number = Column(String(64), nullable=False, unique=True, index=True)
    external_id = Column(String(128), nullable=True, index=True)
    provider_invoice_id = Column(String(128), nullable=True, unique=True, index=True)

    provider = Column(SQLEnum(PaymentProvider), default=PaymentProvider.TIPTOP, nullable=False)
    method = Column(SQLEnum(PaymentMethod), default=PaymentMethod.CARD, nullable=False)
    status = Column(
        SQLEnum(PaymentStatus), default=PaymentStatus.PENDING, nullable=False, index=True
    )

    amount = Column(Numeric(14, 2), nullable=False)
    fee_amount = Column(Numeric(14, 2), nullable=False, default=0)
    refunded_amount = Column(Numeric(14, 2), nullable=False, default=0)
    currency = Column(String(8), nullable=False, default="KZT")

    refund_reason_history = Column(JSONB_TYPE, nullable=True)  # [{amount, reason, at}, ...]
    provider_data = Column(JSONB_TYPE, nullable=True)
    receipt_url = Column(String(1024), nullable=True)
    receipt_number = Column(String(64), nullable=True)

    processed_at = Column(DateTime(False), nullable=True)
    confirmed_at = Column(DateTime(False), nullable=True)
    failed_at = Column(DateTime(False), nullable=True)
    cancelled_at = Column(DateTime(False), nullable=True)

    description = Column(Text, nullable=True)
    customer_ip = Column(INET_TYPE, nullable=True, index=True)
    user_agent = Column(Text, nullable=True)

    failure_reason = Column(String(255), nullable=True)
    failure_code = Column(String(32), nullable=True)

    is_test = Column(Boolean, nullable=False, default=False)

    # Связи
    # Однонаправленная связь к Order — не требуем back_populates на Order
    order = relationship("Order", foreign_keys=[order_id])

    # ✅ СИММЕТРИЧНАЯ СВЯЗЬ С КЛИЕНТОМ (исправление падения мапперов)
    # Customer.payments -> back_populates="customer"
    customer = relationship(
        "Customer",
        back_populates="payments",
        foreign_keys=[customer_id],
        lazy="selectin",
    )

    refunds = relationship(
        "PaymentRefund",
        back_populates="payment",
        cascade="all, delete-orphan",
        foreign_keys=lambda: [PaymentRefund.payment_id],
    )

    __table_args__ = (
        CheckConstraint("amount >= 0", name="ck_payments_amount_nonneg"),
        CheckConstraint("fee_amount >= 0", name="ck_payments_fee_nonneg"),
        CheckConstraint("refunded_amount >= 0", name="ck_payments_refunded_nonneg"),
        CheckConstraint("refunded_amount <= amount", name="ck_payments_refunded_le_amount"),
        CheckConstraint("length(currency) >= 3", name="ck_payments_currency_len"),
        CheckConstraint("version >= 1", name="ck_payments_version_pos"),
        # Частые фильтры
        Index("ix_payments_order_status", "order_id", "status"),
        Index("ix_payments_provider_status", "provider", "status"),
        # GIN по JSONB для быстрых поисков — на непостгресовых диалектах будет проигнорировано
        Index("ix_payments_provider_data_gin", provider_data, postgresql_using="gin"),
        Index("ix_payments_refund_hist_gin", refund_reason_history, postgresql_using="gin"),
    )

    # ---------- Properties ----------
    @property
    def is_successful(self) -> bool:
        return self.status == PaymentStatus.SUCCESS

    @property
    def is_refundable(self) -> bool:
        return self.status in {PaymentStatus.SUCCESS, PaymentStatus.PARTIALLY_REFUNDED} and (
            self.refunded_amount or Decimal("0")
        ) < (self.amount or Decimal("0"))

    @property
    def available_refund_amount(self) -> Decimal:
        amt = Decimal(self.amount or 0)
        ref = Decimal(self.refunded_amount or 0)
        return (amt - ref).quantize(Decimal("0.01"))

    # ---------- Fee Rules ----------
    FEE_RULES: dict[tuple[PaymentProvider, PaymentMethod], dict[str, Decimal]] = {
        (PaymentProvider.TIPTOP, PaymentMethod.CARD): {
            "rate": Decimal("0.012"),
            "fixed": Decimal("50.00"),
            "min": Decimal("80.00"),
            "max": Decimal("3000.00"),
        },
        (PaymentProvider.KASPI, PaymentMethod.QR_CODE): {
            "rate": Decimal("0.009"),
            "fixed": Decimal("0.00"),
            "min": Decimal("50.00"),
            "max": Decimal("1500.00"),
        },
        (PaymentProvider.PAYBOX, PaymentMethod.CARD): {
            "rate": Decimal("0.013"),
            "fixed": Decimal("0.00"),
            "min": Decimal("0.00"),
            "max": Decimal("99999999.00"),
        },
        (PaymentProvider.MANUAL, PaymentMethod.CASH): {
            "rate": Decimal("0.000"),
            "fixed": Decimal("0.00"),
            "min": Decimal("0.00"),
            "max": Decimal("0.00"),
        },
    }

    def apply_fee_rules(self) -> Decimal:
        key = (self.provider, self.method)
        rules = self.FEE_RULES.get(key)
        if not rules:
            return Decimal(self.fee_amount or 0).quantize(Decimal("0.01"))
        amount = Decimal(self.amount or 0)
        calc = (amount * rules["rate"] + rules["fixed"]).quantize(Decimal("0.01"))
        if calc < rules["min"]:
            calc = rules["min"]
        if rules["max"] > 0 and calc > rules["max"]:
            calc = rules["max"]
        current = Decimal(self.fee_amount or 0).quantize(Decimal("0.01"))
        if calc > current:
            self.fee_amount = calc
        return Decimal(self.fee_amount or 0).quantize(Decimal("0.01"))

    # ---------- Domain methods ----------
    def _utcnow(self) -> datetime:
        return datetime.now(UTC).replace(tzinfo=None)

    def mark_processing(self, *, provider_payload: Optional[dict[str, Any]] = None) -> None:
        if self.status not in {PaymentStatus.PENDING, PaymentStatus.PROCESSING}:
            raise ValueError(f"Cannot mark_processing from status {self.status}")
        self.status = PaymentStatus.PROCESSING
        self.processed_at = self.processed_at or self._utcnow()
        if provider_payload:
            self._merge_provider_data(provider_payload)
        self._siem("payment.processing")
        self._bump_version()

    def mark_success(
        self,
        *,
        provider_payload: Optional[dict[str, Any]] = None,
        receipt_url: Optional[str] = None,
        receipt_number: Optional[str] = None,
    ) -> None:
        if self.status not in {
            PaymentStatus.PENDING,
            PaymentStatus.PROCESSING,
            PaymentStatus.FAILED,
        }:
            raise ValueError(f"Cannot mark_success from status {self.status}")
        self.status = PaymentStatus.SUCCESS
        now = self._utcnow()
        self.processed_at = self.processed_at or now
        self.confirmed_at = now
        self.failed_at = None
        self.failure_reason = None
        self.failure_code = None
        if receipt_url:
            self.receipt_url = receipt_url
        if receipt_number:
            self.receipt_number = receipt_number
        if provider_payload:
            self._merge_provider_data(provider_payload)
        self.apply_fee_rules()
        self._siem("payment.succeeded")
        self._enqueue_webhook_event("payment.succeeded", self.to_public_dict())
        self._bump_version()

    def mark_failed(
        self,
        *,
        reason: Optional[str] = None,
        code: Optional[str] = None,
        provider_payload: Optional[dict[str, Any]] = None,
    ) -> None:
        if self.status in {PaymentStatus.SUCCESS, PaymentStatus.REFUNDED}:
            raise ValueError(f"Cannot mark_failed from status {self.status}")
        self.status = PaymentStatus.FAILED
        self.failed_at = self._utcnow()
        if reason:
            self.failure_reason = (reason or "")[:255]
        if code:
            self.failure_code = (code or "")[:32]
        if provider_payload:
            self._merge_provider_data(provider_payload)
        self._siem(
            "payment.failed", extra={"reason": self.failure_reason, "code": self.failure_code}
        )
        self._enqueue_webhook_event("payment.failed", self.to_public_dict())
        self._bump_version()

    def cancel(
        self, *, provider_payload: Optional[dict[str, Any]] = None, reason: Optional[str] = None
    ) -> None:
        if self.status in {PaymentStatus.SUCCESS, PaymentStatus.REFUNDED}:
            raise ValueError(f"Cannot cancel from status {self.status}")
        self.status = PaymentStatus.CANCELLED
        self.cancelled_at = self._utcnow()
        if reason:
            self.failure_reason = (reason or "")[:255]
        if provider_payload:
            self._merge_provider_data(provider_payload)
        self._siem("payment.cancelled", extra={"reason": reason})
        self._enqueue_webhook_event("payment.cancelled", self.to_public_dict())
        self._bump_version()

    def apply_refund(self, amount: Decimal, *, reason: Optional[str] = None) -> Decimal:
        if amount is None:
            raise ValueError("Refund amount is required")
        amount = Decimal(amount).quantize(Decimal("0.01"))
        if amount <= 0:
            raise ValueError("Refund amount must be positive")

        available = self.available_refund_amount
        if amount > available:
            raise ValueError(f"Refund amount {amount} exceeds available {available}")

        self.refunded_amount = (Decimal(self.refunded_amount or 0) + amount).quantize(
            Decimal("0.01")
        )

        if self.refunded_amount == Decimal(self.amount or 0):
            self.status = PaymentStatus.REFUNDED
        else:
            if self.status == PaymentStatus.SUCCESS:
                self.status = PaymentStatus.PARTIALLY_REFUNDED

        entry = {
            "amount": str(amount),
            "reason": (reason or ""),
            "at": self._utcnow().isoformat(timespec="seconds"),
        }
        hist = self.refund_reason_history or []
        if isinstance(hist, str):
            try:
                hist = json.loads(hist)
            except Exception:
                hist = []
        if not isinstance(hist, list):
            hist = []
        hist.append(entry)
        self.refund_reason_history = hist

        self._siem("payment.refund_applied", extra=entry)
        self._enqueue_webhook_event(
            "payment.refund_applied", {"payment": self.to_public_dict(), "entry": entry}
        )
        self._bump_version()
        return self.refunded_amount

    def enqueue_webhook_event(
        self, session: Session, event_type: str, payload: dict[str, Any]
    ) -> IntegrationOutbox:
        out = IntegrationOutbox(
            aggregate_type="payment",
            aggregate_id=self.id,
            event_type=event_type,
            payload=payload or {},
            status=OutboxStatus.PENDING.value,
            attempts=0,
            next_attempt_at=None,
        )
        session.add(out)
        return out

    # ---------- Mixed capture (wallet first, auto-invoice shortfall) ----------
    def capture_mixed_wallet_then_invoice(
        self,
        session: Session,
        *,
        company_id: Optional[int] = None,
        reserve_min: Decimal | int | float = Decimal("0.01"),
        invoice_prefix: str = "INV",
        external_invoice_type: str = "subscription",
        invoice_status: str = "unpaid",
        notes: Optional[str] = None,
        internal_notes: Optional[str] = None,
        wallet_only: bool = False,
    ) -> dict[str, Any]:
        """
        1) Пробуем списать из кошелька, соблюдая резерв >= reserve_min (или ровно 0).
        2) Если не хватило и wallet_only=False — создаём Invoice на точный дефицит.
        3) Если закрыли всю сумму — SUCCESS, иначе PROCESSING (ждём оплату инвойса).
        """
        from app.models.billing import Invoice, WalletBalance  # локальный импорт (без циклов)

        # infer company_id из Order (если есть)
        if company_id is None:
            try:
                if self.order is not None and hasattr(self.order, "company_id"):
                    company_id = int(getattr(self.order, "company_id"))
            except Exception:
                pass
        if company_id is None:
            raise ValueError("company_id is required (or Order.company_id must be present)")

        # Блокируем платёж от конкурентной обработки
        self.lock_for_update(session)

        # Ensure wallet exists
        wallet: WalletBalance | None = session.execute(
            select(WalletBalance).where(WalletBalance.company_id == company_id).with_for_update()
        ).scalar_one_or_none()
        if wallet is None:
            from app.models.billing import WalletBalance as WB

            wallet = WB(company_id=company_id, balance=Decimal("0"))
            session.add(wallet)
            session.flush()

        amount = Decimal(self.amount or 0).quantize(Decimal("0.01"))
        reserve = (
            Decimal(str(reserve_min)).quantize(Decimal("0.01"))
            if reserve_min is not None
            else Decimal("0.00")
        )
        balance = Decimal(wallet.balance or 0).quantize(Decimal("0.01"))

        available_to_debit = (balance - reserve).quantize(Decimal("0.01"), rounding=ROUND_DOWN)
        if available_to_debit < 0:
            available_to_debit = balance

        debit_amount = min(amount, max(Decimal("0.00"), available_to_debit)).quantize(
            Decimal("0.01")
        )
        debited = Decimal("0.00")

        if debit_amount > 0:
            try:
                wallet.debit(
                    debit_amount,
                    session=session,
                    description=f"payment:{self.payment_number}",
                    reference_type="payment",
                    reference_id=self.id,
                )
                debited = debit_amount
            except Exception as e:
                logger.error(
                    "Wallet debit failed for payment %s: %s", self.payment_number, e, exc_info=True
                )
                # не прерываем — ниже fallback в инвойс

        shortfall = (amount - debited).quantize(Decimal("0.01"))
        created_invoice: Optional[Invoice] = None

        if shortfall > 0 and not wallet_only:
            number = Invoice.generate_number(session, prefix=invoice_prefix, company_id=company_id)
            created_invoice = Invoice(
                order_id=getattr(self, "order_id", None),
                company_id=company_id,
                invoice_number=number,
                invoice_type=external_invoice_type,
                subtotal=shortfall,
                tax_amount=Decimal("0"),
                total_amount=shortfall,
                currency=self.currency or "KZT",
                status=invoice_status,
                issue_date=datetime.utcnow(),
                due_date=datetime.utcnow() + timedelta(days=7),
                notes=notes,
                internal_notes=internal_notes,
                payment_id=None,
            )
            session.add(created_invoice)
            session.flush()

        if shortfall == 0:
            self.mark_success()
        else:
            self.mark_processing()

        return {"debited": debited, "shortfall": shortfall, "invoice": created_invoice}

    # ---------- Highload safety / locking ----------
    def lock_for_update(self, session: Session) -> Payment:
        locked = session.execute(
            select(Payment).where(Payment.id == self.id).with_for_update()
        ).scalar_one()
        return locked

    @staticmethod
    def advisory_key_for_payment(payment_id: int) -> int:
        ns = 0x504159  # "PAY"
        return (ns << 32) ^ int(payment_id)

    @contextmanager
    def advisory_lock(self, session: Session):
        key = Payment.advisory_key_for_payment(int(self.id or 0))
        try:
            session.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": key})
        except Exception as e:  # pragma: no cover
            logger.warning("advisory_lock failed for payment %s: %s", self.id, e)
        yield

    @staticmethod
    @asynccontextmanager
    async def advisory_lock_async(session: AsyncSession, payment_id: int):
        key = Payment.advisory_key_for_payment(int(payment_id))
        try:
            await session.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": key})
        except Exception as e:  # pragma: no cover
            logger.warning("advisory_lock_async failed for payment %s: %s", payment_id, e)
        yield

    # ---------- Export / Analytics ----------
    @staticmethod
    def export_query(
        *,
        status_in: Optional[Iterable[PaymentStatus]] = None,
        provider_in: Optional[Iterable[PaymentProvider]] = None,
        date_from: Optional[datetime] = None,
        date_to: Optional[datetime] = None,
        order_id: Optional[int] = None,
        customer_id: Optional[int] = None,
        is_test: Optional[bool] = None,
    ):
        filters = []
        if status_in:
            filters.append(Payment.status.in_(list(status_in)))
        if provider_in:
            filters.append(Payment.provider.in_(list(provider_in)))
        if date_from:
            filters.append(
                or_(
                    and_(Payment.confirmed_at.is_not(None), Payment.confirmed_at >= date_from),
                    and_(Payment.confirmed_at.is_(None), Payment.created_at >= date_from),
                )
            )
        if date_to:
            filters.append(
                or_(
                    and_(Payment.confirmed_at.is_not(None), Payment.confirmed_at < date_to),
                    and_(Payment.confirmed_at.is_(None), Payment.created_at < date_to),
                )
            )
        if order_id is not None:
            filters.append(Payment.order_id == order_id)
        if customer_id is not None:
            filters.append(Payment.customer_id == customer_id)
        if is_test is not None:
            filters.append(Payment.is_test == is_test)

        return (
            select(
                Payment.id,
                Payment.payment_number,
                Payment.order_id,
                Payment.customer_id,
                Payment.provider,
                Payment.method,
                Payment.status,
                Payment.amount,
                Payment.fee_amount,
                Payment.refunded_amount,
                Payment.currency,
                Payment.confirmed_at,
                Payment.created_at,
                Payment.is_test,
            )
            .where(and_(*filters) if filters else True)
            .order_by(Payment.created_at.desc())
        )

    @staticmethod
    def export_as_dicts(session: Session, **kwargs) -> list[dict[str, Any]]:
        rows = session.execute(Payment.export_query(**kwargs)).all()
        out: list[dict[str, Any]] = []
        for r in rows:
            (
                pid,
                pay_num,
                oid,
                cid,
                provider,
                method,
                status,
                amount,
                fee,
                refunded,
                currency,
                confirmed_at,
                created_at,
                is_test,
            ) = r
            out.append(
                {
                    "payment_id": pid,
                    "payment_number": pay_num,
                    "order_id": oid,
                    "customer_id": cid,
                    "provider": provider.value
                    if isinstance(provider, PaymentProvider)
                    else str(provider),
                    "method": method.value if isinstance(method, PaymentMethod) else str(method),
                    "status": status.value if isinstance(status, PaymentStatus) else str(status),
                    "amount": str(amount) if amount is not None else None,
                    "fee_amount": str(fee) if fee is not None else None,
                    "refunded_amount": str(refunded) if refunded is not None else None,
                    "currency": currency,
                    "confirmed_at": confirmed_at.isoformat(timespec="seconds")
                    if confirmed_at
                    else None,
                    "created_at": created_at.isoformat(timespec="seconds") if created_at else None,
                    "is_test": bool(is_test),
                }
            )
        return out

    # NEW: async export
    @staticmethod
    async def export_as_dicts_async(session: AsyncSession, **kwargs) -> list[dict[str, Any]]:
        q = Payment.export_query(**kwargs)
        rows = (await session.execute(q)).all()
        out: list[dict[str, Any]] = []
        for r in rows:
            (
                pid,
                pay_num,
                oid,
                cid,
                provider,
                method,
                status,
                amount,
                fee,
                refunded,
                currency,
                confirmed_at,
                created_at,
                is_test,
            ) = r
            out.append(
                {
                    "payment_id": pid,
                    "payment_number": pay_num,
                    "order_id": oid,
                    "customer_id": cid,
                    "provider": provider.value
                    if isinstance(provider, PaymentProvider)
                    else str(provider),
                    "method": method.value if isinstance(method, PaymentMethod) else str(method),
                    "status": status.value if isinstance(status, PaymentStatus) else str(status),
                    "amount": str(amount) if amount is not None else None,
                    "fee_amount": str(fee) if fee is not None else None,
                    "refunded_amount": str(refunded) if refunded is not None else None,
                    "currency": currency,
                    "confirmed_at": confirmed_at.isoformat(timespec="seconds")
                    if confirmed_at
                    else None,
                    "created_at": created_at.isoformat(timespec="seconds") if created_at else None,
                    "is_test": bool(is_test),
                }
            )
        return out

    @staticmethod
    def analytics_by_status_provider(
        session: Session,
        *,
        date_from: Optional[datetime] = None,
        date_to: Optional[datetime] = None,
        provider_in: Optional[Iterable[PaymentProvider]] = None,
        status_in: Optional[Iterable[PaymentStatus]] = None,
        is_test: Optional[bool] = None,
        customer_id: Optional[int] = None,
    ) -> list[dict[str, Any]]:
        filters = []
        if date_from:
            filters.append(
                or_(
                    and_(Payment.confirmed_at.is_not(None), Payment.confirmed_at >= date_from),
                    and_(Payment.confirmed_at.is_(None), Payment.created_at >= date_from),
                )
            )
        if date_to:
            filters.append(
                or_(
                    and_(Payment.confirmed_at.is_not(None), Payment.confirmed_at < date_to),
                    and_(Payment.confirmed_at.is_(None), Payment.created_at < date_to),
                )
            )
        if provider_in:
            filters.append(Payment.provider.in_(list(provider_in)))
        if status_in:
            filters.append(Payment.status.in_(list(status_in)))
        if is_test is not None:
            filters.append(Payment.is_test == is_test)
        if customer_id is not None:
            filters.append(Payment.customer_id == customer_id)

        q = (
            select(
                Payment.provider,
                Payment.status,
                func.count(Payment.id),
                func.coalesce(func.sum(Payment.amount), 0),
                func.coalesce(func.sum(Payment.fee_amount), 0),
                func.coalesce(func.sum(Payment.refunded_amount), 0),
            )
            .where(and_(*filters))
            .group_by(Payment.provider, Payment.status)
            .order_by(Payment.provider, Payment.status)
        )
        rows = session.execute(q).all()
        return [
            {
                "provider": p.value if isinstance(p, PaymentProvider) else str(p),
                "status": s.value if isinstance(s, PaymentStatus) else str(s),
                "count": int(c or 0),
                "amount_sum": str(sa) if sa is not None else "0.00",
                "fee_sum": str(sf) if sf is not None else "0.00",
                "refunded_sum": str(sr) if sr is not None else "0.00",
            }
            for (p, s, c, sa, sf, sr) in rows
        ]

    # ---------- Refunds history analytics ----------
    @staticmethod
    def refunds_history_analytics(
        session: Session,
        *,
        customer_id: Optional[int] = None,
        date_from: Optional[datetime] = None,
        date_to: Optional[datetime] = None,
        reason_ilike: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        where_clauses: list[str] = []
        params: dict[str, Any] = {}

        if customer_id is not None:
            where_clauses.append("p.customer_id = :customer_id")
            params["customer_id"] = customer_id

        if date_from is not None:
            where_clauses.append("(elem->>'at')::timestamp >= :df")
            params["df"] = date_from
        if date_to is not None:
            where_clauses.append("(elem->>'at')::timestamp < :dt")
            params["dt"] = date_to

        if reason_ilike:
            where_clauses.append("(elem->>'reason') ILIKE :rpat")
            params["rpat"] = f"%{reason_ilike}%"

        where_sql = " AND ".join(where_clauses) if where_clauses else "TRUE"

        sql = text(
            f"""
            SELECT
              COALESCE(NULLIF(TRIM(elem->>'reason'), ''), 'unspecified') AS reason,
              COUNT(*) AS cnt,
              COALESCE(SUM( (elem->>'amount')::numeric ), 0)::numeric(14,2) AS amount_sum
            FROM payments p,
                 LATERAL jsonb_array_elements(p.refund_reason_history) AS elem
            WHERE {where_sql}
            GROUP BY 1
            ORDER BY 2 DESC, 3 DESC
        """
        )
        rows = session.execute(sql, params).all()
        return [{"reason": r[0], "count": int(r[1]), "amount_sum": str(r[2])} for r in rows]

    # ---------- Fraud analytics ----------
    @staticmethod
    def fraud_stats_by_ip(
        session: Session,
        *,
        window_from: Optional[datetime] = None,
        window_to: Optional[datetime] = None,
        min_count: int = 5,
    ) -> list[dict[str, Any]]:
        filters = [Payment.customer_ip.is_not(None)]
        if window_from:
            filters.append(Payment.created_at >= window_from)
        if window_to:
            filters.append(Payment.created_at < window_to)
        q = (
            select(
                Payment.customer_ip,
                func.count(Payment.id).label("cnt"),
                func.sum(func.case((Payment.status == PaymentStatus.FAILED, 1), else_=0)).label(
                    "failed_cnt"
                ),
                func.sum(func.case((Payment.status == PaymentStatus.SUCCESS, 1), else_=0)).label(
                    "succ_cnt"
                ),
            )
            .where(and_(*filters))
            .group_by(Payment.customer_ip)
            .having(func.count(Payment.id) >= min_count)
            .order_by(func.count(Payment.id).desc())
        )
        rows = session.execute(q).all()
        out = []
        for ip, cnt, failed, succ in rows:
            out.append(
                {
                    "customer_ip": str(ip),
                    "count": int(cnt or 0),
                    "failed": int(failed or 0),
                    "success": int(succ or 0),
                    "fail_rate": float((failed or 0) / max(cnt, 1)),
                }
            )
        return out

    @staticmethod
    def fraud_stats_by_user_agent(
        session: Session,
        *,
        window_from: Optional[datetime] = None,
        window_to: Optional[datetime] = None,
        min_count: int = 10,
        top_n: int = 100,
    ) -> list[dict[str, Any]]:
        filters = [Payment.user_agent.is_not(None)]
        if window_from:
            filters.append(Payment.created_at >= window_from)
        if window_to:
            filters.append(Payment.created_at < window_to)
        q = (
            select(
                Payment.user_agent,
                func.count(Payment.id).label("cnt"),
                func.sum(func.case((Payment.status == PaymentStatus.FAILED, 1), else_=0)).label(
                    "failed_cnt"
                ),
                func.sum(func.case((Payment.status == PaymentStatus.SUCCESS, 1), else_=0)).label(
                    "succ_cnt"
                ),
            )
            .where(and_(*filters))
            .group_by(Payment.user_agent)
            .order_by(func.count(Payment.id).desc())
            .limit(top_n)
        )
        rows = session.execute(q).all()
        out = []
        for ua, cnt, failed, succ in rows:
            out.append(
                {
                    "user_agent": ua[:200] if ua else None,
                    "count": int(cnt or 0),
                    "failed": int(failed or 0),
                    "success": int(succ or 0),
                    "fail_rate": float((failed or 0) / max(cnt, 1)),
                }
            )
        return out

    # ---------- Helpers ----------
    def _merge_provider_data(self, payload: dict[str, Any]) -> None:
        try:
            current = self.provider_data or {}
            if isinstance(current, str):
                current = json.loads(current)
            if not isinstance(current, dict):
                current = {"_raw": current}
            current.update(payload or {})
            self.provider_data = current
        except Exception:
            self.provider_data = payload or {}

    def _bump_version(self) -> None:
        self.version = int(self.version or 1) + 1

    def _siem(self, event_name: str, *, extra: Optional[dict[str, Any]] = None) -> None:
        payload = {
            "event": event_name,
            "payment_id": self.id,
            "payment_number": self.payment_number,
            "status": self.status.value if self.status else None,
            "provider": self.provider.value if self.provider else None,
            "method": self.method.value if self.method else None,
            "amount": str(self.amount) if self.amount is not None else None,
            "currency": self.currency,
            "customer_id": self.customer_id,
            "customer_ip": str(self.customer_ip) if self.customer_ip else None,
            "is_test": self.is_test,
            "ts": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        }
        if extra:
            payload.update(extra)
        _emit_siem(payload)

    def _enqueue_webhook_event(self, event_type: str, payload: dict[str, Any]) -> None:
        pending: list[tuple[str, dict[str, Any]]] = getattr(self, "_pending_outbox", [])
        pending.append((event_type, payload))
        setattr(self, "_pending_outbox", pending)

    # ---------- Serialization ----------
    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "uuid": str(self.uuid) if self.uuid else None,
            "version": self.version,
            "order_id": self.order_id,
            "customer_id": self.customer_id,
            "payment_number": self.payment_number,
            "external_id": self.external_id,
            "provider_invoice_id": self.provider_invoice_id,
            "provider": self.provider.value if self.provider else None,
            "method": self.method.value if self.method else None,
            "status": self.status.value if self.status else None,
            "amount": str(self.amount) if self.amount is not None else None,
            "fee_amount": str(self.fee_amount) if self.fee_amount is not None else None,
            "refunded_amount": str(self.refunded_amount)
            if self.refunded_amount is not None
            else None,
            "currency": self.currency,
            "refund_reason_history": self.refund_reason_history,
            "provider_data": self.provider_data,
            "receipt_url": self.receipt_url,
            "receipt_number": self.receipt_number,
            "processed_at": self._iso(self.processed_at),
            "confirmed_at": self._iso(self.confirmed_at),
            "failed_at": self._iso(self.failed_at),
            "cancelled_at": self._iso(self.cancelled_at),
            "description": self.description,
            "customer_ip": self.customer_ip,
            "user_agent": self.user_agent,
            "failure_reason": self.failure_reason,
            "failure_code": self.failure_code,
            "is_test": self.is_test,
            "created_at": self._iso(self.created_at),
            "updated_at": self._iso(self.updated_at),
        }

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "uuid": str(self.uuid) if self.uuid else None,
            "order_id": self.order_id,
            "customer_id": self.customer_id,
            "payment_number": self.payment_number,
            "provider": self.provider.value if self.provider else None,
            "method": self.method.value if self.method else None,
            "status": self.status.value if self.status else None,
            "amount": str(self.amount) if self.amount is not None else None,
            "fee_amount": str(self.fee_amount) if self.fee_amount is not None else None,
            "refunded_amount": str(self.refunded_amount)
            if self.refunded_amount is not None
            else None,
            "currency": self.currency,
            "refund_reason_history": self.refund_reason_history,
            "receipt_url": self.receipt_url,
            "confirmed_at": self._iso(self.confirmed_at),
            "created_at": self._iso(self.created_at),
            "is_test": self.is_test,
        }

    @staticmethod
    def _iso(dt: Optional[datetime]) -> Optional[str]:
        return dt.isoformat(timespec="seconds") if dt else None

    def __repr__(self):
        return f"<Payment(id={self.id}, number='{self.payment_number}', status='{self.status}')>"

    # ---------- Test factory ----------
    @classmethod
    def factory(
        cls,
        order_id: int,
        *,
        amount: Decimal | int | float = 100,
        currency: str = "KZT",
        provider: PaymentProvider = PaymentProvider.TIPTOP,
        method: PaymentMethod = PaymentMethod.CARD,
        status: PaymentStatus = PaymentStatus.PENDING,
        is_test: bool = False,
        customer_id: Optional[int] = None,
        description: Optional[str] = None,
    ) -> Payment:
        return cls(
            order_id=order_id,
            amount=Decimal(str(amount)),
            currency=(currency or "KZT").upper(),
            provider=provider,
            method=method,
            status=status,
            is_test=is_test,
            customer_id=customer_id,
            description=description,
            payment_number=f"PAY-{datetime.utcnow():%Y%m%d}-{uuid.uuid4().hex[:6].upper()}",
        )


# =========================
# REFUNDS
# =========================


class PaymentRefund(Base):
    __tablename__ = "payment_refunds"

    id = Column(Integer, primary_key=True, index=True)
    uuid = Column(
        PG_UUID_TYPE, unique=True, nullable=False, default=lambda: str(uuid.uuid4()), index=True
    )
    version = Column(Integer, nullable=False, default=1)

    created_at = Column(DateTime(False), nullable=False, server_default=func.now(), index=True)
    updated_at = Column(
        DateTime(False), nullable=False, server_default=func.now(), onupdate=func.now(), index=True
    )

    payment_id = Column(ForeignKey("payments.id", ondelete="CASCADE"), nullable=False, index=True)

    refund_number = Column(String(64), nullable=False, unique=True, index=True)
    external_id = Column(String(128), nullable=True, index=True)

    amount = Column(Numeric(14, 2), nullable=False)
    currency = Column(String(8), default="KZT", nullable=False)

    status = Column(
        SQLEnum(PaymentStatus), default=PaymentStatus.PENDING, nullable=False, index=True
    )

    processed_at = Column(DateTime(False), nullable=True)
    completed_at = Column(DateTime(False), nullable=True)
    failed_at = Column(DateTime(False), nullable=True)

    reason = Column(String(255), nullable=True)
    notes = Column(Text, nullable=True)

    provider_data = Column(JSONB_TYPE, nullable=True)

    payment = relationship("Payment", back_populates="refunds", foreign_keys=[payment_id])

    __table_args__ = (
        CheckConstraint("amount > 0", name="ck_payment_refunds_amount_pos"),
        CheckConstraint("length(currency) >= 3", name="ck_payment_refunds_currency_len"),
        CheckConstraint("version >= 1", name="ck_payment_refunds_version_pos"),
        Index("ix_payment_refunds_payment_status", "payment_id", "status"),
        Index("ix_payment_refunds_provider_data_gin", provider_data, postgresql_using="gin"),
    )

    def _utcnow(self) -> datetime:
        return datetime.now(UTC).replace(tzinfo=None)

    def mark_processing(self, *, provider_payload: Optional[dict[str, Any]] = None) -> None:
        if self.status not in {PaymentStatus.PENDING, PaymentStatus.PROCESSING}:
            raise ValueError(f"Cannot mark_processing from status {self.status}")
        self.status = PaymentStatus.PROCESSING
        self.processed_at = self.processed_at or self._utcnow()
        if provider_payload:
            self._merge_provider_data(provider_payload)
        self._siem("refund.processing")
        self._bump_version()

    def mark_completed(self, *, provider_payload: Optional[dict[str, Any]] = None) -> None:
        if self.status not in {PaymentStatus.PENDING, PaymentStatus.PROCESSING}:
            raise ValueError(f"Cannot mark_completed from status {self.status}")
        self.status = PaymentStatus.SUCCESS
        self.completed_at = self._utcnow()
        if provider_payload:
            self._merge_provider_data(provider_payload)
        self._siem("refund.completed")
        self._enqueue_webhook_event("refund.completed", self.to_public_dict())
        self._bump_version()

    def mark_failed(
        self, *, reason: Optional[str] = None, provider_payload: Optional[dict[str, Any]] = None
    ) -> None:
        if self.status in {PaymentStatus.SUCCESS}:
            raise ValueError(f"Cannot mark_failed from status {self.status}")
        self.status = PaymentStatus.FAILED
        self.failed_at = self._utcnow()
        if reason:
            self.reason = (reason or "")[:255]
        if provider_payload:
            self._merge_provider_data(provider_payload)
        self._siem("refund.failed", extra={"reason": self.reason})
        self._enqueue_webhook_event("refund.failed", self.to_public_dict())
        self._bump_version()

    def enqueue_webhook_event(
        self, session: Session, event_type: str, payload: dict[str, Any]
    ) -> IntegrationOutbox:
        out = IntegrationOutbox(
            aggregate_type="payment_refund",
            aggregate_id=self.id,
            event_type=event_type,
            payload=payload or {},
            status=OutboxStatus.PENDING.value,
            attempts=0,
            next_attempt_at=None,
        )
        session.add(out)
        return out

    def _merge_provider_data(self, payload: dict[str, Any]) -> None:
        try:
            current = self.provider_data or {}
            if isinstance(current, str):
                current = json.loads(current)
            if not isinstance(current, dict):
                current = {"_raw": current}
            current.update(payload or {})
            self.provider_data = current
        except Exception:
            self.provider_data = payload or {}

    def _bump_version(self) -> None:
        self.version = int(self.version or 1) + 1

    def _siem(self, event_name: str, *, extra: Optional[dict[str, Any]] = None) -> None:
        payload = {
            "event": event_name,
            "refund_id": self.id,
            "refund_number": self.refund_number,
            "status": self.status.value if self.status else None,
            "payment_id": self.payment_id,
            "amount": str(self.amount) if self.amount is not None else None,
            "currency": self.currency,
            "ts": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        }
        if extra:
            payload.update(extra)
        _emit_siem(payload)

    def _enqueue_webhook_event(self, event_type: str, payload: dict[str, Any]) -> None:
        pending: list[tuple[str, dict[str, Any]]] = getattr(self, "_pending_outbox", [])
        pending.append((event_type, payload))
        setattr(self, "_pending_outbox", pending)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "uuid": str(self.uuid) if self.uuid else None,
            "version": self.version,
            "payment_id": self.payment_id,
            "refund_number": self.refund_number,
            "external_id": self.external_id,
            "amount": str(self.amount) if self.amount is not None else None,
            "currency": self.currency,
            "status": self.status.value if self.status else None,
            "processed_at": self._iso(self.processed_at),
            "completed_at": self._iso(self.completed_at),
            "failed_at": self._iso(self.failed_at),
            "reason": self.reason,
            "notes": self.notes,
            "provider_data": self.provider_data,
            "created_at": self._iso(self.created_at),
            "updated_at": self._iso(self.updated_at),
        }

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "uuid": str(self.uuid) if self.uuid else None,
            "payment_id": self.payment_id,
            "refund_number": self.refund_number,
            "amount": str(self.amount) if self.amount is not None else None,
            "currency": self.currency,
            "status": self.status.value if self.status else None,
            "completed_at": self._iso(self.completed_at),
            "created_at": self._iso(self.created_at),
        }

    @staticmethod
    def _iso(dt: Optional[datetime]) -> Optional[str]:
        return dt.isoformat(timespec="seconds") if dt else None

    def __repr__(self):
        return f"<PaymentRefund(id={self.id}, number='{self.refund_number}', amount={self.amount})>"

    # ---------- Test factory ----------
    @classmethod
    def factory(
        cls,
        payment_id: int,
        *,
        amount: Decimal | int | float = 10,
        currency: str = "KZT",
        status: PaymentStatus = PaymentStatus.PENDING,
        reason: Optional[str] = None,
    ) -> PaymentRefund:
        return cls(
            payment_id=payment_id,
            amount=Decimal(str(amount)),
            currency=(currency or "KZT").upper(),
            status=status,
            reason=reason,
            refund_number=f"REF-{datetime.utcnow():%Y%m%d}-{uuid.uuid4().hex[:6].upper()}",
        )


# =========================
# RECONCILIATION (сверка с провайдерами)
# =========================


class ProviderReconciliation(Base):
    """
    Запись об элементе сверки (строка из стейтмента или результат сопоставления).
    """

    __tablename__ = "provider_reconciliation"

    id = Column(Integer, primary_key=True, index=True)
    created_at = Column(DateTime(False), nullable=False, server_default=func.now(), index=True)

    provider = Column(SQLEnum(PaymentProvider), nullable=False, index=True)
    statement_at = Column(DateTime(False), nullable=False, index=True)

    external_id = Column(String(128), nullable=False, index=True)  # id в провайдере
    amount = Column(Numeric(14, 2), nullable=False)
    currency = Column(String(8), nullable=False, default="KZT")

    matched_payment_id = Column(
        Integer, ForeignKey("payments.id", ondelete="SET NULL"), nullable=True, index=True
    )
    status = Column(SQLEnum(ReconciliationStatus), nullable=False, index=True)
    details = Column(JSONB_TYPE, nullable=True)  # любые дифы, поля провайдера и т.д.

    __table_args__ = (
        Index("ix_recon_provider_external", "provider", "external_id", unique=False),
        CheckConstraint("length(currency) >= 3", name="ck_recon_currency_len"),
    )

    @staticmethod
    def reconcile_statement(
        session: Session,
        *,
        provider: PaymentProvider,
        statement_at: datetime,
        items: Sequence[dict[str, Any]],
        amount_tol: Decimal = Decimal("0.01"),
        currency: str = "KZT",
    ) -> dict[str, int]:
        """
        items: [{external_id, amount, currency?, meta?...}]
        Правила:
          - ищем платеж по external_id (payments.external_id)
          - сравниваем сумму/валюту; статус должен быть SUCCESS/REFUNDED/PARTIALLY_REFUNDED
        """
        created = matched = mismatched = missing = 0
        for it in items:
            ext_id = it.get("external_id")
            amt = Decimal(str(it.get("amount", "0"))).quantize(Decimal("0.01"))
            cur = (it.get("currency") or currency).upper()

            pay = session.execute(
                select(Payment).where(Payment.external_id == ext_id, Payment.provider == provider)
            ).scalar_one_or_none()

            if not pay:
                rec = ProviderReconciliation(
                    provider=provider,
                    statement_at=statement_at,
                    external_id=ext_id,
                    amount=amt,
                    currency=cur,
                    status=ReconciliationStatus.MISSING,
                    details={"item": it},
                )
                session.add(rec)
                created += 1
                missing += 1
                continue

            pay_amt = Decimal(pay.amount or 0).quantize(Decimal("0.01"))
            pay_cur = (pay.currency or "KZT").upper()
            ok_amount = abs(pay_amt - amt) <= amount_tol
            ok_currency = pay_cur == cur
            ok_status = pay.status in {
                PaymentStatus.SUCCESS,
                PaymentStatus.PARTIALLY_REFUNDED,
                PaymentStatus.REFUNDED,
            }

            if ok_amount and ok_currency and ok_status:
                rec = ProviderReconciliation(
                    provider=provider,
                    statement_at=statement_at,
                    external_id=ext_id,
                    amount=amt,
                    currency=cur,
                    matched_payment_id=pay.id,
                    status=ReconciliationStatus.MATCHED,
                    details={"payment_id": pay.id},
                )
                matched += 1
            else:
                rec = ProviderReconciliation(
                    provider=provider,
                    statement_at=statement_at,
                    external_id=ext_id,
                    amount=amt,
                    currency=cur,
                    matched_payment_id=pay.id,
                    status=ReconciliationStatus.MISMATCH,
                    details={
                        "payment_id": pay.id,
                        "payment_amount": str(pay_amt),
                        "payment_currency": pay_cur,
                        "payment_status": pay.status.value,
                        "ok_amount": ok_amount,
                        "ok_currency": ok_currency,
                        "ok_status": ok_status,
                        "item": it,
                    },
                )
                mismatched += 1
            session.add(rec)
            created += 1
        return {
            "created": created,
            "matched": matched,
            "mismatched": mismatched,
            "missing": missing,
        }


# =========================
# EVENTS / HOOKS
# =========================


def _gen_number(prefix: str) -> str:
    today = datetime.utcnow()
    return f"{prefix}-{today:%Y%m%d}-{uuid.uuid4().hex[:6].upper()}"


@event.listens_for(Payment, "before_insert")
def payment_before_insert(mapper, connection, target: Payment):  # pragma: no cover
    if not target.payment_number:
        target.payment_number = _gen_number("PAY")
    target.currency = (target.currency or "KZT").upper()
    target.amount = Decimal(target.amount or 0).quantize(Decimal("0.01"))
    target.fee_amount = Decimal(target.fee_amount or 0).quantize(Decimal("0.01"))
    target.refunded_amount = Decimal(target.refunded_amount or 0).quantize(Decimal("0.01"))
    if target.amount < 0 or target.fee_amount < 0 or target.refunded_amount < 0:
        raise ValueError("Amounts must be non-negative")
    if target.refunded_amount > target.amount:
        raise ValueError("refunded_amount cannot exceed amount")
    try:
        target.apply_fee_rules()
    except Exception as e:
        logger.error("apply_fee_rules failed before_insert: %s", e, exc_info=True)


@event.listens_for(Payment, "before_update")
def payment_before_update(mapper, connection, target: Payment):  # pragma: no cover
    if target.amount is not None:
        target.amount = Decimal(target.amount).quantize(Decimal("0.01"))
    if target.fee_amount is not None:
        target.fee_amount = Decimal(target.fee_amount).quantize(Decimal("0.01"))
    if target.refunded_amount is not None:
        target.refunded_amount = Decimal(target.refunded_amount).quantize(Decimal("0.01"))
    if target.refunded_amount > target.amount:
        raise ValueError("refunded_amount cannot exceed amount")


@event.listens_for(PaymentRefund, "before_insert")
def refund_before_insert(mapper, connection, target: PaymentRefund):  # pragma: no cover
    if not target.refund_number:
        target.refund_number = _gen_number("REF")
    target.currency = (target.currency or "KZT").upper()
    target.amount = Decimal(target.amount or 0).quantize(Decimal("0.01"))
    if target.amount <= 0:
        raise ValueError("Refund amount must be > 0")


@event.listens_for(PaymentRefund, "before_update")
def refund_before_update(mapper, connection, target: PaymentRefund):  # pragma: no cover
    if target.amount is not None:
        target.amount = Decimal(target.amount).quantize(Decimal("0.01"))
    if target.amount <= 0:
        raise ValueError("Refund amount must be > 0")


# after_flush — выносим буфер событий в Outbox
from sqlalchemy.orm import Session as SASession  # avoid name clash above


@event.listens_for(SASession, "after_flush")
def after_flush(session, flush_context):  # pragma: no cover
    for obj in list(session.new) + list(session.dirty):
        pending: list[tuple[str, dict[str, Any]]] = getattr(obj, "_pending_outbox", None)
        if not pending:
            continue
        try:
            if isinstance(obj, Payment):
                for event_type, payload in pending:
                    out = IntegrationOutbox(
                        aggregate_type="payment",
                        aggregate_id=obj.id,
                        event_type=event_type,
                        payload=payload or {},
                        status=OutboxStatus.PENDING.value,
                        attempts=0,
                    )
                    session.add(out)
            elif isinstance(obj, PaymentRefund):
                for event_type, payload in pending:
                    out = IntegrationOutbox(
                        aggregate_type="payment_refund",
                        aggregate_id=obj.id,
                        event_type=event_type,
                        payload=payload or {},
                        status=OutboxStatus.PENDING.value,
                        attempts=0,
                    )
                    session.add(out)
        except Exception as e:
            logger.error("after_flush outbox enqueue failed: %s", e, exc_info=True)
        finally:
            setattr(obj, "_pending_outbox", [])


# =========================
# SIEM EMITTER (Kafka / Sentry / ELK log)
# =========================


def _emit_siem(payload: dict[str, Any]) -> None:
    """
    Fail-safe multi-sink SIEM emitter:
    - Kafka (env KAFKA_BROKERS, KAFKA_TOPIC_SIEM) — best effort
    - Sentry (if sentry_sdk is installed) — breadcrumb/event
    - ELK (JSON log line)
    """
    try:
        _emit_kafka(payload)
    except Exception as e:
        logger.debug("SIEM Kafka emit failed: %s", e, exc_info=False)
    try:
        _emit_sentry(payload)
    except Exception as e:
        logger.debug("SIEM Sentry emit skipped/failed: %s", e, exc_info=False)
    try:
        logger.info("SIEM_EVENT %s", json.dumps(payload, ensure_ascii=False))
    except Exception:
        logger.info("SIEM_EVENT %s", payload)


def _emit_kafka(payload: dict[str, Any]) -> None:
    brokers = os.getenv("KAFKA_BROKERS")
    topic = os.getenv("KAFKA_TOPIC_SIEM", "siem.events")
    if not brokers:
        return  # not configured
    try:
        from confluent_kafka import Producer  # type: ignore
    except Exception:
        return
    conf = {"bootstrap.servers": brokers}
    p = Producer(conf)
    p.produce(topic, json.dumps(payload).encode("utf-8"))
    p.flush(1.0)


def _emit_sentry(payload: dict[str, Any]) -> None:
    try:
        import sentry_sdk  # type: ignore
    except Exception:
        return
    sentry_sdk.capture_message(f"SIEM_EVENT: {payload.get('event')}", level="info")
    # по желанию — breadcrumbs/contexts


__all__ = [
    "Payment",
    "PaymentRefund",
    "IntegrationOutbox",
    "ProviderReconciliation",
    "PaymentStatus",
    "PaymentMethod",
    "PaymentProvider",
    "OutboxStatus",
    "ReconciliationStatus",
]
