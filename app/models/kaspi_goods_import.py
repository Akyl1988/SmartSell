from __future__ import annotations

from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID

from app.models.base import Base


class KaspiGoodsImport(Base):
    __tablename__ = "kaspi_goods_imports"

    id = Column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    company_id = Column(ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True)
    created_by_user_id = Column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    merchant_uid = Column(String(128), nullable=True, index=True)
    import_code = Column(String(128), nullable=False, index=True)
    status = Column(String(64), nullable=False, server_default=text("'created'"))
    source = Column(String(32), nullable=True, index=True)
    comment = Column(Text, nullable=True)
    request_json = Column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    status_json = Column(JSONB, nullable=True)
    result_json = Column(JSONB, nullable=True)
    error_code = Column(String(64), nullable=True)
    error_message = Column(Text, nullable=True)
    last_error_at = Column(DateTime, nullable=True)
    last_checked_at = Column(DateTime, nullable=True)
    revoked_at = Column(DateTime, nullable=True)

    raw_response = Column(Text, nullable=True)

    request_payload = Column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    result_payload = Column(JSONB, nullable=True)
    last_error = Column(Text, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
