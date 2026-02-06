"""RBAC helpers for platform- vs store-level admin checks."""
from __future__ import annotations

from typing import Any


def is_platform_admin(user: Any) -> bool:
    if user is None:
        return False
    if getattr(user, "is_superuser", False):
        return True
    role = (getattr(user, "role", "") or "").lower()
    return role in {"platform_admin", "superadmin"}


def is_store_admin(user: Any) -> bool:
    if user is None:
        return False
    is_admin = getattr(user, "is_admin", None)
    if callable(is_admin):
        return bool(is_admin())
    role = (getattr(user, "role", "") or "").lower()
    return role == "admin" or getattr(user, "is_superuser", False)


__all__ = ["is_platform_admin", "is_store_admin"]
