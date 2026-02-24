from __future__ import annotations

from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, Index, Integer, Numeric, String, UniqueConstraint, desc, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship

from app.models.base import Base


class KaspiCatalogItem(Base):
    __tablename__ = "kaspi_catalog_items"

    id = Column(Integer, primary_key=True)
    company_id = Column(ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True)
    merchant_uid = Column(String(128), nullable=False, index=True)
    sku = Column(String(128), nullable=False)
    offer_code = Column(String(128), nullable=True)
    product_code = Column(String(128), nullable=True)
    last_seen_name = Column(String(255), nullable=True)
    last_seen_price = Column(Numeric(18, 2), nullable=True)
    last_seen_qty = Column(Integer, nullable=True)
    last_seen_at = Column(DateTime, nullable=True)
    raw = Column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    company = relationship("Company", backref="kaspi_catalog_items")

    __table_args__ = (
        UniqueConstraint("company_id", "merchant_uid", "sku", name="uq_kaspi_catalog_items_company_merchant_sku"),
        Index("ix_kaspi_catalog_items_company_merchant", "company_id", "merchant_uid"),
        Index("ix_kaspi_catalog_items_company_sku", "company_id", "sku"),
        Index(
            "ix_kaspi_catalog_items_company_merchant_last_seen",
            "company_id",
            "merchant_uid",
            desc("last_seen_at"),
        ),
    )
