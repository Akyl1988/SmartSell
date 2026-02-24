from __future__ import annotations

from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID

from app.models.base import Base


class KaspiImportRun(Base):
    __tablename__ = "kaspi_import_runs"

    id = Column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    company_id = Column(ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True)
    merchant_uid = Column(String(128), nullable=False, index=True)
    import_code = Column(String(64), nullable=False, index=True)
    kaspi_import_code = Column(String(128), nullable=True, index=True)
    status = Column(String(64), nullable=False, server_default=text("'created'"))
    request_id = Column(String(128), nullable=True, index=True)
    error_code = Column(String(64), nullable=True)
    error_message = Column(Text, nullable=True)
    payload_hash = Column(String(64), nullable=True)
    attempts = Column(Integer, nullable=False, server_default=text("0"))
    request_payload = Column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    status_json = Column(JSONB, nullable=True)
    result_json = Column(JSONB, nullable=True)
    last_checked_at = Column(DateTime, nullable=True)
    next_poll_at = Column(DateTime, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
