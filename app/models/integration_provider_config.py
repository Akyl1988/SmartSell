from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import BaseModel, LenientInitMixin


class IntegrationProviderConfig(LenientInitMixin, BaseModel):
    __tablename__ = "integration_provider_configs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    domain: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String(128), nullable=False)
    config_encrypted: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    key_id: Mapped[str] = mapped_column(String(64), nullable=False, default="master")
    meta_json: Mapped[Any | None] = mapped_column(
        JSON().with_variant(postgresql.JSONB(astext_type=Text()), "postgresql"), nullable=True
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (UniqueConstraint("domain", "provider", name="uq__integration_provider_configs__domain_provider"),)


__all__ = ["IntegrationProviderConfig"]
