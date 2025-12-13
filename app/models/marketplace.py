# app/models/marketplace.py
from __future__ import annotations
from sqlalchemy.orm import Mapped, mapped_column, DeclarativeBase
from sqlalchemy import BigInteger, String, Boolean, Text, Numeric
from sqlalchemy.sql import func
from typing import Optional

# В проекте уже есть Base; если она в другом месте — импортируйте оттуда.
from app.core.db import Base  # придерживаемся ваших конвенций (см. память проекта)

class KaspiStoreToken(Base):
    __tablename__ = "kaspi_store_tokens"
    __table_args__ = {"schema": "public"}
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    company_external_id: Mapped[Optional[str]] = mapped_column(String(64))
    store_id: Mapped[Optional[str]] = mapped_column(String(64), unique=True)
    provider: Mapped[str] = mapped_column(String(32), default="kaspi", nullable=False)
    key_id: Mapped[Optional[str]] = mapped_column(String(128))
    api_key_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    meta: Mapped[Optional[str]] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[str] = mapped_column(default=func.now(), nullable=False)
    updated_at: Mapped[str] = mapped_column(default=func.now(), onupdate=func.now(), nullable=False)

class ProductMarketplacePrice(Base):
    __tablename__ = "product_marketplace_price"
    __table_args__ = (
        # уникальность (product_id, marketplace)
        {'schema': 'public'},
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    product_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    marketplace: Mapped[str] = mapped_column(String(32), nullable=False)
    price: Mapped[str] = mapped_column(Numeric(14, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="KZT", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[str] = mapped_column(default=func.now(), nullable=False)
    updated_at: Mapped[str] = mapped_column(default=func.now(), onupdate=func.now(), nullable=False)
