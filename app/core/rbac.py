"""RBAC v2 helpers for platform vs store access control."""
from __future__ import annotations

from enum import Enum
from typing import Any

from app.core.exceptions import AuthorizationError
from app.core.security import resolve_tenant_company_id


class Role(str, Enum):
    PLATFORM_ADMIN = "platform_admin"
    PLATFORM_MANAGER = "platform_manager"
    STORE_ADMIN = "admin"
    STORE_MANAGER = "manager"
    STORE_EMPLOYEE = "employee"


PLATFORM_ADMIN = Role.PLATFORM_ADMIN.value
PLATFORM_MANAGER = Role.PLATFORM_MANAGER.value
STORE_ADMIN = Role.STORE_ADMIN.value
STORE_MANAGER = Role.STORE_MANAGER.value
STORE_EMPLOYEE = Role.STORE_EMPLOYEE.value
STORE_STAFF_ROLES = {
    Role.STORE_ADMIN.value,
    Role.STORE_MANAGER.value,
    Role.STORE_EMPLOYEE.value,
    "storekeeper",
    "analyst",
}


ROLE_ALIASES: dict[str, str] = {
    "superadmin": Role.PLATFORM_ADMIN.value,
    "super_admin": Role.PLATFORM_ADMIN.value,
    "platform_admin": Role.PLATFORM_ADMIN.value,
    "platform_manager": Role.PLATFORM_MANAGER.value,
    "admin": Role.STORE_ADMIN.value,
    "store_admin": Role.STORE_ADMIN.value,
    "manager": Role.STORE_MANAGER.value,
    "store_manager": Role.STORE_MANAGER.value,
    "employee": Role.STORE_EMPLOYEE.value,
    "store_employee": Role.STORE_EMPLOYEE.value,
    "staff": Role.STORE_EMPLOYEE.value,
}

PLATFORM_ROLES = {Role.PLATFORM_ADMIN.value, Role.PLATFORM_MANAGER.value}
STORE_ROLES = {Role.STORE_ADMIN.value, Role.STORE_MANAGER.value, Role.STORE_EMPLOYEE.value}


def is_superuser(user: Any) -> bool:
    if user is None:
        return False
    from app.core.security import is_superuser as security_is_superuser

    return security_is_superuser(user)


def is_platform_admin(user: Any) -> bool:
    if user is None:
        return False
    if has_role(user, Role.PLATFORM_ADMIN.value):
        return True
    return is_superuser(user)


def is_platform_manager(user: Any) -> bool:
    if user is None:
        return False
    if is_platform_admin(user):
        return True
    return has_role(user, Role.PLATFORM_MANAGER.value)


def is_store_admin(user: Any, company_id: int | None = None) -> bool:
    if user is None:
        return False
    _ = company_id
    if is_superuser(user):
        return False
    is_admin = getattr(user, "is_admin", None)
    if callable(is_admin):
        if normalize_role(getattr(user, "role", "")) == Role.STORE_ADMIN.value:
            return bool(is_admin())
        return False
    return has_role(user, Role.STORE_ADMIN.value)


def is_store_manager(user: Any) -> bool:
    if user is None:
        return False
    if is_superuser(user):
        return False
    return has_role(user, Role.STORE_MANAGER.value)


def is_store_employee(user: Any) -> bool:
    return has_role(user, Role.STORE_EMPLOYEE.value)


def is_store_staff(user: Any) -> bool:
    if user is None:
        return False
    if is_superuser(user):
        return False
    return has_any_role(user, STORE_STAFF_ROLES)


def normalize_role(role: str | None) -> str:
    if not role:
        return ""
    return ROLE_ALIASES.get(str(role).strip().lower(), str(role).strip().lower())


def get_user_roles(user: Any) -> set[str]:
    if user is None:
        return set()
    roles: set[str] = set()
    role_value = getattr(user, "role", None)
    if role_value:
        roles.add(normalize_role(role_value))
    extra_roles = getattr(user, "roles", None)
    if isinstance(extra_roles, list | set | tuple):
        roles.update(normalize_role(r) for r in extra_roles if r)
    if is_superuser(user):
        roles.add(Role.PLATFORM_ADMIN.value)
    return {r for r in roles if r}


def has_role(user: Any, role: str) -> bool:
    if not user:
        return False
    normalized = normalize_role(role)
    return normalized in get_user_roles(user)


def has_any_role(user: Any, roles: set[str]) -> bool:
    if not user:
        return False
    normalized = {normalize_role(r) for r in roles if r}
    return bool(get_user_roles(user) & normalized)


def require_platform_admin(user: Any) -> Any:
    if not is_platform_admin(user):
        raise AuthorizationError("Admin role required", "ADMIN_REQUIRED")
    return user


def require_store_admin(user: Any) -> Any:
    if not is_store_admin(user):
        raise AuthorizationError("Admin role required", "ADMIN_REQUIRED")
    return user


def require_roles(user: Any, *roles: str) -> Any:
    allowed = {normalize_role(r) for r in roles if r}
    if not allowed or not has_any_role(user, allowed):
        raise AuthorizationError("Insufficient role", "FORBIDDEN")
    return user


def get_company_id(user: Any, *, not_found_detail: str = "Company not set") -> int:
    return resolve_tenant_company_id(user, not_found_detail=not_found_detail)


__all__ = [
    "Role",
    "PLATFORM_ADMIN",
    "PLATFORM_MANAGER",
    "STORE_ADMIN",
    "STORE_MANAGER",
    "STORE_EMPLOYEE",
    "STORE_STAFF_ROLES",
    "ROLE_ALIASES",
    "PLATFORM_ROLES",
    "STORE_ROLES",
    "normalize_role",
    "get_user_roles",
    "has_role",
    "has_any_role",
    "is_superuser",
    "is_platform_admin",
    "is_platform_manager",
    "is_store_admin",
    "is_store_manager",
    "is_store_employee",
    "is_store_staff",
    "require_platform_admin",
    "require_store_admin",
    "require_roles",
    "get_company_id",
]
