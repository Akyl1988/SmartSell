from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import BaseModel


class IdempotencyKey(BaseModel):
    """Persistent idempotency key storage (tenant-scoped)."""

    __tablename__ = "idempotency_keys"
    __table_args__ = (
        UniqueConstraint("company_id", "key", name="uq__idempotency_keys__company_id__key"),
    )

    company_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    key: Mapped[str] = mapped_column(String(length=200), nullable=False)
    status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
