from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, Numeric, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB

from app.models.base import Base


class KaspiOffer(Base):
    __tablename__ = "kaspi_offers"

    id = Column(Integer, primary_key=True)
    company_id = Column(ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True)
    merchant_uid = Column(String(128), nullable=False)
    sku = Column(String(128), nullable=False)
    master_sku = Column(String(128), nullable=True)
    title = Column(String(255), nullable=True)
    price = Column(Numeric(18, 2), nullable=True)
    old_price = Column(Numeric(18, 2), nullable=True)
    stock_count = Column(Integer, nullable=True)
    pre_order = Column(Boolean, nullable=True)
    stock_specified = Column(Boolean, nullable=True)
    raw = Column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("company_id", "merchant_uid", "sku", name="uq_kaspi_offers_company_merchant_sku"),
    )
