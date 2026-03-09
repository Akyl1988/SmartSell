from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class AvailabilitySyncIn(BaseModel):
    product_id: int = Field(..., ge=1, description="ID продукта в нашей БД")


class AvailabilityBulkIn(BaseModel):
    limit: int = Field(500, ge=1, le=5000, description="Максимум товаров за одну операцию")


class KaspiMcSessionIn(BaseModel):
    merchant_uid: str = Field(..., min_length=3, max_length=128)
    cookies: str = Field(..., min_length=3)


class KaspiMcSessionOut(BaseModel):
    merchant_uid: str
    is_active: bool
    created_at: datetime
    updated_at: datetime
    last_used_at: datetime | None = None
    last_error: str | None = None
    cookies_masked: str | None = None


class KaspiMcSessionListOut(BaseModel):
    items: list[KaspiMcSessionOut]


class KaspiMcSyncOut(BaseModel):
    rows_total: int
    rows_ok: int
    rows_failed: int
    errors: list[dict[str, Any]] = []


class KaspiTokenMaskedOut(BaseModel):
    """Ответ для карточки токена без раскрытия секрета."""

    id: str
    store_name: str
    token_hex_masked: str
    created_at: Any
    updated_at: Any
    last_selftest_at: datetime | None = None
    last_selftest_status: str | None = None
    last_selftest_error_code: str | None = None
    last_selftest_error_message: str | None = None
