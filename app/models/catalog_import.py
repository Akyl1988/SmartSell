from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID

from app.models.base import Base


class CatalogImportBatch(Base):
    __tablename__ = "catalog_import_batches"

    id = Column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    company_id = Column(ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True)
    source = Column(String(32), nullable=False, server_default=text("'kaspi'"))
    filename = Column(String(255), nullable=False)
    content_hash = Column(String(64), nullable=False, index=True)
    status = Column(String(16), nullable=False, server_default=text("'PENDING'"))
    merchant_uid = Column(String(128), nullable=True)

    rows_total = Column(Integer, nullable=False, server_default=text("0"))
    rows_ok = Column(Integer, nullable=False, server_default=text("0"))
    rows_failed = Column(Integer, nullable=False, server_default=text("0"))

    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)
    error_summary = Column(Text, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class CatalogImportRow(Base):
    __tablename__ = "catalog_import_rows"

    id = Column(Integer, primary_key=True)
    batch_id = Column(ForeignKey("catalog_import_batches.id", ondelete="CASCADE"), nullable=False, index=True)
    company_id = Column(ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True)
    row_num = Column(Integer, nullable=False)
    raw = Column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))

    sku = Column(String(128), nullable=True)
    master_sku = Column(String(128), nullable=True)
    title = Column(String(255), nullable=True)
    price = Column(Integer, nullable=True)
    old_price = Column(Integer, nullable=True)
    stock_count = Column(Integer, nullable=True)
    pre_order = Column(Boolean, nullable=True)
    stock_specified = Column(Boolean, nullable=True)
    updated_at = Column(DateTime, nullable=True)

    error = Column(Text, nullable=True)
