"""Schemas for company users management."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field, field_validator

from app.schemas.base import BaseSchema

_ALLOWED_ROLES = {"admin", "employee", "manager", "storekeeper", "analyst", "platform_admin"}


class UserPublicOut(BaseSchema):
    id: int
    phone: str | None = None
    email: str | None = None
    full_name: str | None = None
    role: str | None = None
    is_active: bool = True
    is_verified: bool = False
    created_at: datetime | None = None


class UsersListOut(BaseSchema):
    items: list[UserPublicOut]


class UserUpdate(BaseSchema):
    full_name: str | None = Field(None, max_length=255)


class UserRoleUpdate(BaseSchema):
    role: Literal["admin", "employee", "manager", "storekeeper", "analyst", "platform_admin"]

    @field_validator("role")
    @classmethod
    def _validate_role(cls, v: str) -> str:
        if v not in _ALLOWED_ROLES:
            raise ValueError("Invalid role")
        return v
