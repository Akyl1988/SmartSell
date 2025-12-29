from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import BYTEA, JSONB
from sqlalchemy.types import JSON, LargeBinary

from app.core.db import Base


class SystemIntegration(Base):
    __tablename__ = "system_integrations"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    domain = Column(String(64), nullable=False, index=True)
    provider = Column(String(128), nullable=False)
    config_encrypted = Column(LargeBinary().with_variant(BYTEA, "postgresql"), nullable=False)
    is_enabled = Column(Boolean, nullable=False, server_default="true")
    capabilities = Column(JSON().with_variant(JSONB, "postgresql"), nullable=True)
    version = Column(Integer, nullable=False, server_default="1")
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    __table_args__ = (
        UniqueConstraint("domain", "provider", name="uq_system_integrations_domain_provider"),
        CheckConstraint("version > 0", name="ck_system_integrations_version_pos"),
    )

    def __repr__(self) -> str:  # pragma: no cover - diagnostics helper
        return f"<SystemIntegration domain={self.domain} provider={self.provider} version={self.version}>"


class SystemActiveProvider(Base):
    __tablename__ = "system_active_providers"

    domain = Column(String(64), primary_key=True)
    provider = Column(String(128), nullable=False)
    version = Column(Integer, nullable=False, server_default="1")
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())

    __table_args__ = (CheckConstraint("version > 0", name="ck_system_active_version_pos"),)

    def __repr__(self) -> str:  # pragma: no cover - diagnostics helper
        return f"<SystemActiveProvider domain={self.domain} provider={self.provider} version={self.version}>"
