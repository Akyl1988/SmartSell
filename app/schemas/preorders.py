"""Preorder schemas for store module."""

from __future__ import annotations

from decimal import Decimal

from pydantic import Field, field_validator, model_validator

from app.schemas.base import BaseSchema, PaginatedResponse, TimestampedSchema

_ALLOWED_STATUSES = {"new", "confirmed", "cancelled", "fulfilled"}


class PreorderItemIn(BaseSchema):
    product_id: int | None = Field(None, ge=1)
    sku: str | None = Field(None, max_length=100)
    name: str | None = Field(None, max_length=255)
    qty: int = Field(..., gt=0)
    price: Decimal | None = Field(None, ge=0, max_digits=14, decimal_places=2)


class PreorderItemOut(PreorderItemIn, TimestampedSchema):
    preorder_id: int


class PreorderCreateIn(BaseSchema):
    currency: str = Field(default="KZT", min_length=3, max_length=8)
    customer_name: str | None = Field(None, max_length=255)
    customer_phone: str | None = Field(None, max_length=32)
    notes: str | None = Field(None, max_length=2000)
    items: list[PreorderItemIn] = Field(default_factory=list)

    @field_validator("currency", mode="before")
    @classmethod
    def _normalize_currency(cls, value: str) -> str:
        return str(value).strip().upper()


class PreorderUpdateIn(BaseSchema):
    customer_name: str | None = Field(None, max_length=255)
    customer_phone: str | None = Field(None, max_length=32)
    notes: str | None = Field(None, max_length=2000)
    items: list[PreorderItemIn] | None = None


class PreorderOut(TimestampedSchema):
    company_id: int
    status: str
    currency: str
    total: Decimal | None
    customer_name: str | None
    customer_phone: str | None
    notes: str | None
    created_by_user_id: int | None
    items: list[PreorderItemOut] | None = None

    @model_validator(mode="after")
    def _validate_status(self) -> PreorderOut:
        if self.status not in _ALLOWED_STATUSES:
            raise ValueError("Invalid preorder status")
        return self


class PreorderListFilters(BaseSchema):
    status: str | None = None
    date_from: str | None = None
    date_to: str | None = None

    @field_validator("status", mode="before")
    @classmethod
    def _normalize_status(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return str(value).strip().lower()


PreorderListResponse = PaginatedResponse[PreorderOut]
