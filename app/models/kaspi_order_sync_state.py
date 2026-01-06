from __future__ import annotations

from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import relationship

from app.models.base import Base


class KaspiOrderSyncState(Base):
    __tablename__ = "kaspi_order_sync_state"

    id = Column(Integer, primary_key=True)
    company_id = Column(ForeignKey("companies.id", ondelete="CASCADE"), nullable=False)
    last_synced_at = Column(DateTime, nullable=True)
    last_external_order_id = Column(String(128), nullable=True)
    last_error_at = Column(DateTime, nullable=True)
    last_error_code = Column(String(64), nullable=True)
    last_error_message = Column(String(500), nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    company = relationship("Company", backref="kaspi_sync_state")

    __table_args__ = (UniqueConstraint("company_id", name="uq_kaspi_sync_state_company"),)
