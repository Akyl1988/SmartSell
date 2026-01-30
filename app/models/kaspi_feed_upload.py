from __future__ import annotations

from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import UUID

from app.models.base import Base


class KaspiFeedUpload(Base):
    __tablename__ = "kaspi_feed_uploads"

    id = Column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    company_id = Column(Integer, ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True)
    merchant_uid = Column(String(128), nullable=False, index=True)
    export_id = Column(Integer, nullable=True, index=True)
    source = Column(String(32), nullable=True, index=True)
    comment = Column(Text, nullable=True)
    status = Column(String(64), nullable=False, server_default=text("'created'"))
    import_code = Column(String(128), nullable=True, index=True)
    attempts = Column(Integer, nullable=False, server_default=text("0"))
    last_error_code = Column(String(64), nullable=True)
    last_error_message = Column(Text, nullable=True)
    last_attempt_at = Column(DateTime, nullable=True)
    request_id = Column(String(128), nullable=True, index=True)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
