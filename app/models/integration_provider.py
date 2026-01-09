from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import BaseModel, LenientInitMixin


class IntegrationProvider(LenientInitMixin, BaseModel):
    __tablename__ = "integration_providers"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    domain: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String(128), nullable=False)
    config_json: Mapped[Any | None] = mapped_column(
        JSON().with_variant(postgresql.JSONB(astext_type=Text()), "postgresql"), nullable=True
    )
    capabilities: Mapped[Any | None] = mapped_column(
        JSON().with_variant(postgresql.JSONB(astext_type=Text()), "postgresql"), nullable=True
    )
    is_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    updated_by_user_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"), nullable=True)

    __table_args__ = (UniqueConstraint("domain", "provider", name="uq__integration_providers__domain_provider"),)


class IntegrationProviderEvent(LenientInitMixin, BaseModel):
    __tablename__ = "integration_provider_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    domain: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    provider_from: Mapped[str | None] = mapped_column(String(128), nullable=True)
    provider_to: Mapped[str] = mapped_column(String(128), nullable=False)
    actor_user_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    meta_json: Mapped[Any | None] = mapped_column(
        JSON().with_variant(postgresql.JSONB(astext_type=Text()), "postgresql"), nullable=True
    )
