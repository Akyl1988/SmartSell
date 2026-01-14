from __future__ import annotations

from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship

from app.models.base import Base


class KaspiFeedExport(Base):
    __tablename__ = "kaspi_feed_exports"

    id = Column(Integer, primary_key=True)
    company_id = Column(ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True)
    kind = Column(String(50), nullable=False)  # e.g. "products"
    format = Column(String(50), nullable=False)  # e.g. "xml"
    status = Column(
        String(50), nullable=False, server_default=text("'generated'")
    )  # generated, uploading, uploaded, failed
    checksum = Column(String(64), nullable=False)  # sha256 hex
    payload_text = Column(Text, nullable=False)  # XML content
    stats_json = Column(JSONB, nullable=True, server_default=text("NULL"))  # {total, active}
    last_error = Column(Text, nullable=True)

    # Retry and diagnostics
    attempts = Column(Integer, nullable=False, server_default=text("0"))
    last_attempt_at = Column(DateTime, nullable=True)
    uploaded_at = Column(DateTime, nullable=True)
    duration_ms = Column(Integer, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    company = relationship("Company", backref="kaspi_feed_exports")

    __table_args__ = (
        UniqueConstraint("company_id", "kind", "checksum", name="uq_kaspi_feed_exports_company_kind_checksum"),
        Index("ix_kaspi_feed_exports_company_created", "company_id", "created_at"),
        Index("ix_kaspi_feed_exports_company_kind", "company_id", "kind"),
    )
