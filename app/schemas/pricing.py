"""Pydantic schemas for repricing rules and preview/apply payloads."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from pydantic import Field, field_validator, model_validator

from app.schemas.base import BaseSchema, PaginatedResponse, TimestampedSchema


class PricingRuleBase(BaseSchema):
    """Shared fields for repricing rules."""

    name: str = Field(..., min_length=1, max_length=255)
    is_active: bool = Field(default=True)
    enabled: bool = Field(default=False)
    scope: dict[str, Any] | None = None

    min_price: Decimal | None = Field(None, ge=0, max_digits=14, decimal_places=2)
    max_price: Decimal | None = Field(None, ge=0, max_digits=14, decimal_places=2)
    step: Decimal | None = Field(None, ge=0, max_digits=14, decimal_places=2)
    undercut: Decimal | None = Field(None, ge=0, max_digits=14, decimal_places=2)
    cooldown_seconds: int | None = Field(None, ge=0)
    max_delta_percent: Decimal | None = Field(None, ge=0, max_digits=5, decimal_places=2)

    @model_validator(mode="after")
    def _validate_min_max(self) -> PricingRuleBase:
        if self.min_price is not None and self.max_price is not None and self.min_price > self.max_price:
            raise ValueError("min_price cannot be greater than max_price")
        return self


class PricingRuleCreate(PricingRuleBase):
    """Schema for creating repricing rules."""

    pass


class PricingRuleUpdate(BaseSchema):
    """Schema for updating repricing rules."""

    name: str | None = Field(None, min_length=1, max_length=255)
    is_active: bool | None = None
    enabled: bool | None = None
    scope: dict[str, Any] | None = None

    min_price: Decimal | None = Field(None, ge=0, max_digits=14, decimal_places=2)
    max_price: Decimal | None = Field(None, ge=0, max_digits=14, decimal_places=2)
    step: Decimal | None = Field(None, ge=0, max_digits=14, decimal_places=2)
    undercut: Decimal | None = Field(None, ge=0, max_digits=14, decimal_places=2)
    cooldown_seconds: int | None = Field(None, ge=0)
    max_delta_percent: Decimal | None = Field(None, ge=0, max_digits=5, decimal_places=2)


class PricingRuleResponse(PricingRuleBase, TimestampedSchema):
    """Rule response schema."""

    company_id: int


class PricingRuleInline(BaseSchema):
    """Inline rule payload for preview."""

    min_price: Decimal | None = Field(None, ge=0, max_digits=14, decimal_places=2)
    max_price: Decimal | None = Field(None, ge=0, max_digits=14, decimal_places=2)
    step: Decimal | None = Field(None, ge=0, max_digits=14, decimal_places=2)
    undercut: Decimal | None = Field(None, ge=0, max_digits=14, decimal_places=2)
    cooldown_seconds: int | None = Field(None, ge=0)
    max_delta_percent: Decimal | None = Field(None, ge=0, max_digits=5, decimal_places=2)

    @model_validator(mode="after")
    def _validate_min_max(self) -> PricingRuleInline:
        if self.min_price is not None and self.max_price is not None and self.min_price > self.max_price:
            raise ValueError("min_price cannot be greater than max_price")
        return self


class PricingProductFilter(BaseSchema):
    """Optional product filters for preview/apply."""

    product_ids: list[int] | None = Field(None, min_length=1)
    category_id: int | None = Field(None, ge=1)
    sku: str | None = Field(None, max_length=100)
    name_contains: str | None = Field(None, max_length=255)
    min_price: Decimal | None = Field(None, ge=0, max_digits=14, decimal_places=2)
    max_price: Decimal | None = Field(None, ge=0, max_digits=14, decimal_places=2)
    is_active: bool | None = None
    limit: int | None = Field(200, ge=1, le=1000)

    @field_validator("sku", mode="before")
    @classmethod
    def _normalize_sku(cls, v: str | None) -> str | None:
        if v is None:
            return v
        return str(v).strip().upper()


class PricingPreviewRequest(BaseSchema):
    """Request payload for preview."""

    rule_id: int | None = Field(None, ge=1)
    rule: PricingRuleInline | None = None
    filters: PricingProductFilter | None = None

    @model_validator(mode="after")
    def _require_rule(self) -> PricingPreviewRequest:
        if not self.rule_id and not self.rule:
            raise ValueError("rule_id or rule must be provided")
        return self


class PricingApplyRequest(BaseSchema):
    """Request payload for apply."""

    rule_id: int = Field(..., ge=1)
    filters: PricingProductFilter | None = None


class PricingPreviewItem(BaseSchema):
    """Preview output per product."""

    product_id: int
    old_price: Decimal | None
    new_price: Decimal | None
    reason: str


class PricingApplyResponse(BaseSchema):
    """Apply response."""

    run_id: int
    stats: dict[str, Any]
    diffs: list[PricingPreviewItem]


PricingRuleListResponse = PaginatedResponse[PricingRuleResponse]
