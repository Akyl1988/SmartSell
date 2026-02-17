from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, condecimal, constr

PlanCode = constr(min_length=2, max_length=32)
FeatureCode = constr(min_length=2, max_length=64)
CurrencyCode = constr(min_length=3, max_length=8)


class PlanCreate(BaseModel):
    code: PlanCode
    name: str = Field(..., min_length=2, max_length=128)
    price: condecimal(max_digits=14, decimal_places=0) = Decimal("0")
    currency: CurrencyCode = "KZT"
    is_active: bool = True
    trial_days_default: int = Field(default=14, ge=0, le=60)


class PlanUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=128)
    price: condecimal(max_digits=14, decimal_places=0) | None = None
    currency: CurrencyCode | None = None
    is_active: bool | None = None
    trial_days_default: int | None = Field(default=None, ge=0, le=60)


class PlanOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    code: str
    name: str
    price: Decimal
    currency: str
    is_active: bool
    trial_days_default: int
    created_at: datetime | None = None
    updated_at: datetime | None = None


class FeatureCreate(BaseModel):
    code: FeatureCode
    name: str = Field(..., min_length=2, max_length=128)
    description: str | None = Field(default=None, max_length=512)
    is_active: bool = True


class FeatureUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=128)
    description: str | None = Field(default=None, max_length=512)
    is_active: bool | None = None


class FeatureOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    code: str
    name: str
    description: str | None = None
    is_active: bool
    created_at: datetime | None = None
    updated_at: datetime | None = None


class PlanFeatureUpsert(BaseModel):
    enabled: bool = True
    limits: dict[str, Any] | None = None


class PlanFeatureOut(BaseModel):
    id: int
    plan_code: str
    feature_code: str
    enabled: bool
    limits: dict[str, Any] | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
