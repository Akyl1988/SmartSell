from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Index, Integer, Numeric, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship

from app.models.base import Base


class KaspiCatalogProduct(Base):
    __tablename__ = "kaspi_catalog_products"

    id = Column(Integer, primary_key=True)
    company_id = Column(ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True)
    offer_id = Column(String(128), nullable=False)
    name = Column(String(255), nullable=True)
    sku = Column(String(128), nullable=True)
    price = Column(Numeric(18, 2), nullable=True)
    qty = Column(Integer, nullable=True)
    is_active = Column(Boolean, nullable=False, server_default=text("true"))
    raw = Column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    company = relationship("Company", backref="kaspi_catalog_products")

    __table_args__ = (
        UniqueConstraint("company_id", "offer_id", name="uq_kaspi_catalog_products_company_offer"),
        Index("ix_kaspi_catalog_products_company_offer", "company_id", "offer_id"),
    )
