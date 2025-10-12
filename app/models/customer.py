from __future__ import annotations

"""
Customer model — production-ready, SQLAlchemy 2.0 style (typed), with clean
indexes, validators and ergonomic helpers. Symmetric relationship to Payment
is defined via explicit foreign_keys to avoid AmbiguousForeignKeysError and to
satisfy back_populates from Payment.customer.

Highlights:
- Declarative 2.0 typing (Mapped / mapped_column)
- Multi-tenant & audit friendly via mixins
- Soft-delete aware helpers and flags
- Useful search/find/get_or_create utilities
- Stable UTC-naive timestamps (works on SQLite/PostgreSQL)
- Proper indexes for typical queries
"""

from collections.abc import Iterable
from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, Index, Integer, String, func
from sqlalchemy.orm import Mapped, Session, mapped_column, relationship, validates

# In this project BaseModel is the DeclarativeBase with naming conventions & metadata.
# Mixins provide deleted_at, tenant_id, audit fields, etc.
from app.models.base import AuditMixin, BaseModel, SoftDeleteMixin, TenantMixin


# ----------------------------------------------------------------------------- #
# Small UTC helper (UTC-naive, as used across the codebase)
# ----------------------------------------------------------------------------- #
def utc_now() -> datetime:
    return datetime.utcnow()


# ----------------------------------------------------------------------------- #
# Model
# ----------------------------------------------------------------------------- #
class Customer(BaseModel, TenantMixin, SoftDeleteMixin, AuditMixin):
    __tablename__ = "customers"
    __allow_unmapped__ = True  # keep legacy compatibility where needed

    # PK
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, index=True)

    # Core fields
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    full_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    phone: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utc_now, nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utc_now, onupdate=utc_now, nullable=False
    )

    # Relationships
    # ВАЖНО: симметрия с Payment.customer (см. app/models/payment.py)
    payments: Mapped[list[Payment]] = relationship(
        "Payment",
        back_populates="customer",
        foreign_keys="Payment.customer_id",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    __table_args__ = (
        Index("ix_customers_active_email", "is_active", "email"),
        Index("ix_customers_phone", "phone"),
        # Частые фильтры по компании и активности (TenantMixin добавляет tenant/company поле, если есть)
        # Защищаемся: если поля нет на конкретной схеме — SQLAlchemy просто проигнорирует при миграциях тестов.
    )

    # ------------------------- Validators / Normalizers ---------------------- #
    @validates("email")
    def _v_email(self, _k: str, v: str) -> str:
        v = (v or "").strip().lower()
        if not v or "@" not in v:
            raise ValueError("Invalid email")
        return v

    @validates("phone")
    def _v_phone(self, _k: str, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        return v.strip()

    @validates("full_name")
    def _v_fullname(self, _k: str, v: Optional[str]) -> Optional[str]:
        return v.strip() if isinstance(v, str) else v

    # ------------------------------- Flags ---------------------------------- #
    @property
    def is_archived(self) -> bool:
        return getattr(self, "deleted_at", None) is not None

    def activate(self) -> None:
        if not self.is_archived:
            self.is_active = True

    def deactivate(self) -> None:
        self.is_active = False

    def soft_delete(self) -> None:
        # SoftDeleteMixin usually has deleted_at field; we just set it
        self.is_active = False
        setattr(self, "deleted_at", utc_now())

    def restore(self) -> None:
        setattr(self, "deleted_at", None)
        self.is_active = True

    # ---------------------------- Serialization ----------------------------- #
    def to_dict(self, *, exclude_private: bool = True) -> dict:
        data = {
            "id": self.id,
            "email": self.email,
            "full_name": self.full_name,
            "phone": self.phone,
            "is_active": self.is_active,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "deleted_at": getattr(self, "deleted_at", None),
        }
        # Добавьте здесь tenant_id/ audit поля при необходимости
        return data

    def to_public_dict(self) -> dict:
        d = self.to_dict()
        d["display_name"] = self.display_name
        d["is_archived"] = self.is_archived
        d["created_at_iso"] = (
            self.created_at.isoformat(timespec="seconds") if self.created_at else None
        )
        d["updated_at_iso"] = (
            self.updated_at.isoformat(timespec="seconds") if self.updated_at else None
        )
        return d

    @property
    def display_name(self) -> str:
        if self.full_name:
            return self.full_name
        if self.email:
            return self.email
        if self.phone:
            return self.phone
        return f"Customer#{self.id}"

    def anonymized_dict(self) -> dict:
        d = self.to_public_dict()
        if d.get("email"):
            loc, _, dom = d["email"].partition("@")
            d["email"] = (loc[:1] + "***@" + dom) if dom else "***"
        if d.get("phone"):
            ph = d["phone"]
            d["phone"] = "***" + ph[-3:] if isinstance(ph, str) and len(ph) > 3 else "***"
        return d

    # ----------------------------- Query helpers ---------------------------- #
    @classmethod
    def find_by_email(cls, session: Session, email: str) -> Optional[Customer]:
        if not email:
            return None
        return session.query(cls).filter(func.lower(cls.email) == email.strip().lower()).first()

    @classmethod
    def find_by_phone(cls, session: Session, phone: str) -> Optional[Customer]:
        if not phone:
            return None
        return session.query(cls).filter(cls.phone == phone.strip()).first()

    @classmethod
    def search(
        cls, session: Session, q: str, *, limit: int = 50, include_archived: bool = False
    ) -> list[Customer]:
        query = session.query(cls)
        if not include_archived:
            query = query.filter(getattr(cls, "deleted_at", None).is_(None))
        if q:
            like = f"%{q.strip().lower()}%"
            query = query.filter(
                func.lower(cls.email).like(like)
                | func.lower(func.coalesce(cls.full_name, "")).like(like)
                | func.coalesce(cls.phone, "").like(like)
            )
        return query.order_by(cls.created_at.desc()).limit(limit).all()

    @classmethod
    def get_or_create(
        cls,
        session: Session,
        *,
        email: str,
        defaults: Optional[dict] = None,
    ) -> tuple[Customer, bool]:
        obj = cls.find_by_email(session, email)
        if obj:
            return obj, False
        data = {"email": email.strip().lower(), **(defaults or {})}
        obj = cls(**data)
        session.add(obj)
        session.commit()
        return obj, True

    @classmethod
    def bulk_deactivate(cls, session: Session, ids: Iterable[int]) -> int:
        count = 0
        for c in session.query(cls).filter(cls.id.in_(list(ids))).all():
            if c.is_active:
                c.deactivate()
                count += 1
        session.flush()
        return count

    @classmethod
    def bulk_restore(cls, session: Session, ids: Iterable[int]) -> int:
        count = 0
        for c in session.query(cls).filter(cls.id.in_(list(ids))).all():
            if c.is_archived:
                c.restore()
                count += 1
        session.flush()
        return count

    # ------------------------------ Dunder ---------------------------------- #
    def __repr__(self) -> str:
        return f"<Customer id={self.id} email={self.email!r} active={self.is_active}>"


__all__ = ["Customer"]
