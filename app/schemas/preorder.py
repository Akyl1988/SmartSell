"""Preorder schemas."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import Field

from app.models.preorder import PreorderStatus
from app.schemas.base import BaseSchema, PaginatedResponse, TimestampedSchema


class PreorderCreate(BaseSchema):
    product_id: int = Field(..., ge=1)
    qty: int = Field(..., gt=0)
    customer_name: str | None = Field(None, max_length=255)
    customer_phone: str | None = Field(None, max_length=32)
    comment: str | None = Field(None, max_length=2000)


class PreorderResponse(TimestampedSchema):
    company_id: int
    product_id: int
    qty: int
    customer_name: str | None
    customer_phone: str | None
    comment: str | None
    status: PreorderStatus
    preorder_until_snapshot: datetime | None
    deposit_snapshot: Decimal | None
    converted_order_id: int | None


class PreorderListFilters(BaseSchema):
    status: PreorderStatus | None = None
    product_id: int | None = Field(None, ge=1)
    date_from: str | None = None
    date_to: str | None = None


class PreorderListResponse(PaginatedResponse[PreorderResponse]):
    pass
