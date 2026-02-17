"""Schemas for store-level repricing rules and runs."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import Field, field_validator, model_validator

from app.schemas.base import BaseSchema, PaginatedResponse, TimestampedSchema


class RepricingRuleBase(BaseSchema):
    name: str = Field(..., min_length=1, max_length=255)
    enabled: bool = Field(default=True)
    scope_type: str = Field(default="all", max_length=32)
    scope_value: str | None = Field(default=None, max_length=255)

    min_price: Decimal | None = Field(None, ge=0, max_digits=14, decimal_places=2)
    max_price: Decimal | None = Field(None, ge=0, max_digits=14, decimal_places=2)
    step: Decimal | None = Field(None, ge=0, max_digits=14, decimal_places=2)
    rounding_mode: str | None = Field(default="nearest", max_length=32)

    @field_validator("scope_type", mode="before")
    @classmethod
    def _normalize_scope_type(cls, value: str | None) -> str:
        if not value:
            return "all"
        return str(value).strip().lower()

    @field_validator("rounding_mode", mode="before")
    @classmethod
    def _normalize_rounding(cls, value: str | None) -> str | None:
        if value is None:
            return "nearest"
        return str(value).strip().lower()

    @model_validator(mode="after")
    def _validate_bounds(self) -> RepricingRuleBase:
        if self.min_price is not None and self.max_price is not None and self.min_price > self.max_price:
            raise ValueError("min_price cannot be greater than max_price")
        return self


class RepricingRuleCreate(RepricingRuleBase):
    is_active: bool = Field(default=True)


class RepricingRuleUpdate(BaseSchema):
    name: str | None = Field(None, min_length=1, max_length=255)
    enabled: bool | None = None
    is_active: bool | None = None
    scope_type: str | None = Field(default=None, max_length=32)
    scope_value: str | None = Field(default=None, max_length=255)

    min_price: Decimal | None = Field(None, ge=0, max_digits=14, decimal_places=2)
    max_price: Decimal | None = Field(None, ge=0, max_digits=14, decimal_places=2)
    step: Decimal | None = Field(None, ge=0, max_digits=14, decimal_places=2)
    rounding_mode: str | None = Field(default=None, max_length=32)

    @field_validator("scope_type", mode="before")
    @classmethod
    def _normalize_scope_type(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return str(value).strip().lower()

    @field_validator("rounding_mode", mode="before")
    @classmethod
    def _normalize_rounding(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return str(value).strip().lower()


class RepricingRuleResponse(RepricingRuleBase, TimestampedSchema):
    company_id: int
    is_active: bool


class RepricingRunItemResponse(TimestampedSchema):
    run_id: int
    product_id: int | None
    old_price: Decimal | None
    new_price: Decimal | None
    reason: str | None
    status: str | None
    error: str | None


class RepricingRunResponse(TimestampedSchema):
    company_id: int
    status: str
    started_at: datetime | None
    finished_at: datetime | None
    processed: int | None
    changed: int | None
    failed: int | None
    last_error: str | None
    request_id: str | None
    triggered_by_user_id: int | None
    items: list[RepricingRunItemResponse] | None = None


class RepricingRunTriggerResponse(BaseSchema):
    run_id: int


RepricingRuleListResponse = PaginatedResponse[RepricingRuleResponse]
RepricingRunListResponse = PaginatedResponse[RepricingRunResponse]
