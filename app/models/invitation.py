from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import CheckConstraint, Column, DateTime, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import validates

from app.models.base import Base


def utc_now() -> datetime:
    return datetime.utcnow()


def _normalize_phone(v: str | None) -> str:
    return "".join(ch for ch in (v or "") if ch.isdigit() or ch == "+").strip()


def _normalize_email(v: str | None) -> str:
    vv = (v or "").strip().lower()
    return vv


class InvitationToken(Base):
    __tablename__ = "invitation_tokens"
    __table_args__ = (
        UniqueConstraint("token_hash", name="uq_invitation_tokens_token_hash"),
        Index("ix_invitation_tokens_expires_at", "expires_at"),
        Index("ix_invitation_tokens_token_hash", "token_hash"),
        Index("ix_invitation_tokens_company_id", "company_id"),
        CheckConstraint("role in ('admin','employee')", name="ck_invitation_role"),
    )

    id = Column(Integer, primary_key=True, index=True)
    company_id = Column(Integer, ForeignKey("companies.id", ondelete="CASCADE"), nullable=False)
    role = Column(String(32), nullable=False, default="employee")
    email = Column(String(255), nullable=True)
    phone = Column(String(32), nullable=False)
    display_name = Column(String(255), nullable=True)
    token_hash = Column(String(128), nullable=False)
    expires_at = Column(DateTime, nullable=False)
    used_at = Column(DateTime, nullable=True)
    created_by_user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime, nullable=False, default=utc_now)
    meta = Column(JSONB, nullable=True)

    @validates("email")
    def _v_email(self, _k: str, v: str | None) -> str | None:
        if v is None:
            return None
        return _normalize_email(v)

    @validates("phone")
    def _v_phone(self, _k: str, v: str) -> str:
        return _normalize_phone(v)

    @validates("role")
    def _v_role(self, _k: str, v: str) -> str:
        return (v or "employee").strip().lower()

    def mark_used(self) -> None:
        self.used_at = utc_now()

    @classmethod
    def build(
        cls,
        *,
        company_id: int,
        role: str,
        phone: str,
        token_hash: str,
        email: Optional[str] = None,
        display_name: Optional[str] = None,
        ttl_hours: int = 72,
        created_by_user_id: Optional[int] = None,
        meta: Optional[str] = None,
    ) -> InvitationToken:
        now = utc_now()
        return cls(
            company_id=company_id,
            role=(role or "employee").strip().lower(),
            email=_normalize_email(email),
            phone=_normalize_phone(phone),
            display_name=display_name.strip() if display_name else None,
            token_hash=token_hash,
            expires_at=now + timedelta(hours=max(ttl_hours, 1)),
            created_at=now,
            created_by_user_id=created_by_user_id,
            meta=meta,
        )

    def is_used(self) -> bool:
        return self.used_at is not None

    def is_expired(self, now: Optional[datetime] = None) -> bool:
        now = now or utc_now()
        return self.expires_at <= now


class PasswordResetToken(Base):
    __tablename__ = "password_reset_tokens"
    __table_args__ = (
        UniqueConstraint("token_hash", name="uq_password_reset_tokens_token_hash"),
        Index("ix_password_reset_tokens_expires_at", "expires_at"),
        Index("ix_password_reset_tokens_token_hash", "token_hash"),
    )

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    token_hash = Column(String(128), nullable=False)
    expires_at = Column(DateTime, nullable=False)
    used_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=utc_now)
    requested_ip = Column(String(45), nullable=True)
    user_agent = Column(String(255), nullable=True)

    def mark_used(self) -> None:
        self.used_at = utc_now()

    @classmethod
    def build(
        cls,
        *,
        user_id: int,
        token_hash: str,
        ttl_minutes: int = 10,
        requested_ip: Optional[str] = None,
        user_agent: Optional[str] = None,
    ) -> PasswordResetToken:
        now = utc_now()
        return cls(
            user_id=user_id,
            token_hash=token_hash,
            expires_at=now + timedelta(minutes=max(ttl_minutes, 1)),
            created_at=now,
            requested_ip=requested_ip,
            user_agent=user_agent,
        )

    def is_used(self) -> bool:
        return self.used_at is not None

    def is_expired(self, now: Optional[datetime] = None) -> bool:
        now = now or utc_now()
        return self.expires_at <= now
