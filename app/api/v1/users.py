"""
User management endpoints (enterprise-ready).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, Path, Query
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_async_db
from app.core.dependencies import api_rate_limit, get_current_user, get_current_verified_user
from app.core.exceptions import AuthenticationError, AuthorizationError, NotFoundError, SmartSellValidationError
from app.core.logging import audit_logger
from app.core.rbac import is_platform_admin, is_store_admin
from app.core.security import get_password_hash, resolve_tenant_company_id, verify_password
from app.models.company import Company
from app.models.user import User, UserSession
from app.schemas.base import SuccessResponse
from app.schemas.user import UserResponse, UserUpdate
from app.schemas.users import UserPublicOut, UserRoleUpdate, UsersListOut
from app.schemas.users import UserUpdate as CompanyUserUpdate

router = APIRouter(prefix="/users", tags=["users"], dependencies=[Depends(api_rate_limit)])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _get_company_and_owner(db: AsyncSession, company_id: int) -> Company | None:
    res = await db.execute(select(Company).where(Company.id == company_id))
    return res.scalars().first()


def _require_owner_or_admin(*, current_user: User, company: Company | None) -> bool:
    is_owner = bool(company and company.owner_id == current_user.id)
    if is_owner or is_store_admin(current_user) or is_platform_admin(current_user):
        return is_owner
    raise AuthorizationError("Insufficient permissions", "FORBIDDEN")


# ---------------------------------------------------------------------------
# Me
# ---------------------------------------------------------------------------


@router.get("", response_model=UsersListOut)
async def list_company_users(
    active_only: bool | None = Query(None),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    company_id = resolve_tenant_company_id(current_user)
    company = await _get_company_and_owner(db, company_id)
    _require_owner_or_admin(current_user=current_user, company=company)

    stmt = select(User).where(User.company_id == company_id)
    if active_only:
        stmt = stmt.where(User.is_active.is_(True))
    res = await db.execute(stmt.order_by(User.id.asc()))
    users = res.scalars().all()

    items = [
        UserPublicOut(
            id=u.id,
            phone=u.phone,
            email=u.email,
            full_name=getattr(u, "full_name", None),
            role=getattr(u, "role", None),
            is_active=bool(getattr(u, "is_active", True)),
            is_verified=bool(getattr(u, "is_verified", False)),
            created_at=getattr(u, "created_at", None),
        )
        for u in users
    ]
    return UsersListOut(items=items)


@router.patch("/{user_id}", response_model=UserPublicOut)
async def update_company_user(
    user_id: int = Path(...),
    payload: CompanyUserUpdate = Body(...),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    company_id = resolve_tenant_company_id(current_user)
    company = await _get_company_and_owner(db, company_id)
    _require_owner_or_admin(current_user=current_user, company=company)

    res = await db.execute(select(User).where(User.id == user_id, User.company_id == company_id))
    user = res.scalars().first()
    if not user:
        raise NotFoundError("User not found", "USER_NOT_FOUND")

    data = payload.model_dump(exclude_unset=True)
    if "full_name" in data:
        user.full_name = data["full_name"]
        await db.commit()

    if "full_name" in data:
        audit_logger.log_data_change(
            user_id=current_user.id,
            action="update",
            resource_type="user",
            resource_id=str(user_id),
            changes={"full_name": data.get("full_name")},
        )

    return UserPublicOut(
        id=user.id,
        phone=user.phone,
        email=user.email,
        full_name=user.full_name,
        role=user.role,
        is_active=user.is_active,
        is_verified=user.is_verified,
        created_at=getattr(user, "created_at", None),
    )


def _is_owner(company: Company | None, user: User) -> bool:
    return bool(company and company.owner_id == user.id)


@router.post("/{user_id}/deactivate", response_model=SuccessResponse)
async def deactivate_user(
    user_id: int = Path(...),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    company_id = resolve_tenant_company_id(current_user)
    company = await _get_company_and_owner(db, company_id)
    is_owner = _is_owner(company, current_user)
    is_admin = is_store_admin(current_user)
    if not (is_owner or is_admin):
        raise AuthorizationError("Insufficient permissions", "FORBIDDEN")

    if current_user.id == user_id:
        raise SmartSellValidationError("Cannot deactivate self", "SELF_ACTION_FORBIDDEN")

    res = await db.execute(select(User).where(User.id == user_id, User.company_id == company_id))
    user = res.scalars().first()
    if not user:
        raise NotFoundError("User not found", "USER_NOT_FOUND")

    if company and company.owner_id == user.id:
        raise AuthorizationError("Cannot deactivate owner", "OWNER_PROTECTED")

    target_role = (getattr(user, "role", "") or "").lower()
    if is_admin and not is_owner and target_role != "employee":
        raise AuthorizationError("Admin can deactivate only employees", "FORBIDDEN")

    user.is_active = False
    await db.execute(update(UserSession).where(UserSession.user_id == user.id).values(is_active=False))
    await db.commit()

    audit_logger.log_data_change(
        user_id=current_user.id,
        action="deactivate",
        resource_type="user",
        resource_id=str(user_id),
        changes={"is_active": False},
    )
    return SuccessResponse(message="User deactivated")


@router.post("/{user_id}/activate", response_model=SuccessResponse)
async def activate_user(
    user_id: int = Path(...),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    company_id = resolve_tenant_company_id(current_user)
    company = await _get_company_and_owner(db, company_id)
    is_owner = _is_owner(company, current_user)
    is_admin = is_store_admin(current_user)
    if not (is_owner or is_admin):
        raise AuthorizationError("Insufficient permissions", "FORBIDDEN")

    if current_user.id == user_id:
        raise SmartSellValidationError("Cannot activate self", "SELF_ACTION_FORBIDDEN")

    res = await db.execute(select(User).where(User.id == user_id, User.company_id == company_id))
    user = res.scalars().first()
    if not user:
        raise NotFoundError("User not found", "USER_NOT_FOUND")

    if company and company.owner_id == user.id:
        raise AuthorizationError("Cannot modify owner", "OWNER_PROTECTED")

    target_role = (getattr(user, "role", "") or "").lower()
    if is_admin and not is_owner and target_role != "employee":
        raise AuthorizationError("Admin can activate only employees", "FORBIDDEN")

    user.is_active = True
    await db.commit()

    audit_logger.log_data_change(
        user_id=current_user.id,
        action="activate",
        resource_type="user",
        resource_id=str(user_id),
        changes={"is_active": True},
    )
    return SuccessResponse(message="User activated")


@router.post("/{user_id}/role", response_model=SuccessResponse)
async def change_user_role(
    user_id: int = Path(...),
    payload: UserRoleUpdate = Body(...),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    company_id = resolve_tenant_company_id(current_user)
    company = await _get_company_and_owner(db, company_id)
    is_owner = _is_owner(company, current_user)
    is_admin = is_store_admin(current_user)
    if not (is_owner or is_admin):
        raise AuthorizationError("Insufficient permissions", "FORBIDDEN")

    if current_user.id == user_id:
        raise SmartSellValidationError("Cannot change own role", "SELF_ACTION_FORBIDDEN")

    res = await db.execute(select(User).where(User.id == user_id, User.company_id == company_id))
    user = res.scalars().first()
    if not user:
        raise NotFoundError("User not found", "USER_NOT_FOUND")

    if company and company.owner_id == user.id:
        raise AuthorizationError("Cannot change owner role", "OWNER_PROTECTED")

    target_role = (payload.role or "").lower()
    if target_role == "admin" and not is_owner:
        raise AuthorizationError("Only owner can assign admin", "OWNER_REQUIRED")
    if is_admin and not is_owner and target_role in {"admin", "platform_admin"}:
        raise AuthorizationError("Admin cannot assign this role", "FORBIDDEN")

    user.role = target_role
    await db.commit()

    audit_logger.log_data_change(
        user_id=current_user.id,
        action="role_change",
        resource_type="user",
        resource_id=str(user_id),
        changes={"role": payload.role},
    )
    return SuccessResponse(message="Role updated")


@router.get("/me", response_model=UserResponse)
async def get_current_user_info(current_user: User = Depends(get_current_user)):
    """Get current user information."""
    return current_user


@router.put("/me", response_model=UserResponse)
async def update_current_user(
    user_update: UserUpdate,
    current_user: User = Depends(get_current_verified_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Update current user information."""
    db_user = await db.get(User, current_user.id)
    if not db_user:
        raise AuthenticationError("User not found", "USER_NOT_FOUND")

    changes: dict[str, dict[str, Any]] = {}

    for field, value in user_update.model_dump(exclude_unset=True).items():
        if hasattr(db_user, field):
            old = getattr(db_user, field)
            if old != value:
                setattr(db_user, field, value)
                changes[field] = {"old": old, "new": value}

    if changes:
        await db.commit()
        await db.refresh(db_user)

        audit_logger.log_data_change(
            user_id=db_user.id,
            action="update",
            resource_type="user",
            resource_id=str(db_user.id),
            changes=changes,
        )

    return db_user


@router.delete("/me", response_model=SuccessResponse)
async def deactivate_current_user(
    current_user: User = Depends(get_current_verified_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Deactivate current user account (soft)."""
    db_user = await db.get(User, current_user.id)
    if not db_user:
        raise AuthenticationError("User not found", "USER_NOT_FOUND")

    if not db_user.is_active:
        return SuccessResponse(message="Account already inactive")

    old = db_user.is_active
    db_user.is_active = False

    await db.execute(
        update(UserSession)
        .where(UserSession.user_id == current_user.id, UserSession.is_active.is_(True))
        .values(is_active=False)
    )

    await db.commit()

    audit_logger.log_data_change(
        user_id=db_user.id,
        action="deactivate",
        resource_type="user",
        resource_id=str(db_user.id),
        changes={"is_active": {"old": old, "new": False}},
    )

    return SuccessResponse(message="Account deactivated successfully")


@router.post("/me/reactivate", response_model=SuccessResponse)
async def reactivate_current_user(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Reactivate current user account."""
    db_user = await db.get(User, current_user.id)
    if not db_user:
        raise AuthenticationError("User not found", "USER_NOT_FOUND")

    if db_user.is_active:
        return SuccessResponse(message="Account already active")

    old = db_user.is_active
    db_user.is_active = True
    await db.commit()

    audit_logger.log_data_change(
        user_id=db_user.id,
        action="reactivate",
        resource_type="user",
        resource_id=str(db_user.id),
        changes={"is_active": {"old": old, "new": True}},
    )
    return SuccessResponse(message="Account reactivated")


# ---------------------------------------------------------------------------
# Security: change password & sessions
# ---------------------------------------------------------------------------

from pydantic import BaseModel, Field


class ChangePasswordRequest(BaseModel):
    old_password: str = Field(..., min_length=6)
    new_password: str = Field(..., min_length=8)


@router.post("/me/change-password", response_model=SuccessResponse)
async def change_password(
    req: ChangePasswordRequest,
    current_user: User = Depends(get_current_verified_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Change password and invalidate all active sessions."""
    db_user = await db.get(User, current_user.id)
    if not db_user:
        raise AuthenticationError("User not found", "USER_NOT_FOUND")

    if not verify_password(req.old_password, db_user.hashed_password):
        raise AuthenticationError("Old password is incorrect", "INVALID_OLD_PASSWORD")

    if req.old_password == req.new_password:
        raise SmartSellValidationError("New password must be different", "PASSWORD_SAME")

    db_user.hashed_password = get_password_hash(req.new_password)

    await db.execute(
        update(UserSession)
        .where(UserSession.user_id == db_user.id, UserSession.is_active.is_(True))
        .values(is_active=False)
    )

    await db.commit()

    audit_logger.log_data_change(
        user_id=db_user.id,
        action="change_password",
        resource_type="user",
        resource_id=str(db_user.id),
        changes={"password": {"old": "***", "new": "***"}},
    )

    return SuccessResponse(message="Password changed successfully")


@router.get("/me/sessions", response_model=list[dict])
async def list_my_sessions(
    current_user: User = Depends(get_current_verified_user),
    db: AsyncSession = Depends(get_async_db),
):
    """List active and expired sessions for current user."""
    res = await db.execute(
        select(UserSession).where(UserSession.user_id == current_user.id).order_by(UserSession.created_at.desc())
    )
    sessions = res.scalars().all()
    result = []
    for s in sessions:
        result.append(
            {
                "id": s.id,
                "is_active": s.is_active,
                "created_at": getattr(s, "created_at", None),
                "expires_at": getattr(s, "expires_at", None),
                "ip_address": getattr(s, "ip_address", None),
                "user_agent": getattr(s, "user_agent", None),
            }
        )
    return result


@router.post("/me/sessions/revoke", response_model=SuccessResponse)
async def revoke_my_sessions(
    session_ids: list[int],
    current_user: User = Depends(get_current_verified_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Revoke selected sessions of the current user."""
    if not session_ids:
        raise SmartSellValidationError("No session ids provided", "NO_IDS")

    result = await db.execute(
        update(UserSession)
        .where(
            UserSession.user_id == current_user.id,
            UserSession.id.in_(session_ids),
            UserSession.is_active.is_(True),
        )
        .values(is_active=False)
    )
    affected = int(result.rowcount or 0)
    await db.commit()

    if affected:
        audit_logger.log_data_change(
            user_id=current_user.id,
            action="revoke_sessions",
            resource_type="user_session",
            resource_id="*",
            changes={"count": affected, "ids": session_ids},
        )

    return SuccessResponse(message="Selected sessions revoked", data={"revoked": affected})


@router.post("/me/sessions/revoke_all", response_model=SuccessResponse)
async def revoke_all_my_sessions(
    current_user: User = Depends(get_current_verified_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Revoke all active sessions of the current user."""
    result = await db.execute(
        update(UserSession)
        .where(UserSession.user_id == current_user.id, UserSession.is_active.is_(True))
        .values(is_active=False)
    )
    affected = int(result.rowcount or 0)
    await db.commit()

    if affected:
        audit_logger.log_data_change(
            user_id=current_user.id,
            action="revoke_all_sessions",
            resource_type="user_session",
            resource_id="*",
            changes={"count": affected},
        )
    return SuccessResponse(message="All sessions revoked", data={"revoked": affected})


# ---------------------------------------------------------------------------
# Public profiles
# ---------------------------------------------------------------------------


@router.get("/{user_id}", response_model=UserResponse)
async def get_user_public_profile(
    user_id: int = Path(..., ge=1),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Public profile by id (без приватных полей).
    Если в модели есть чувствительные поля, предполагается что Pydantic-схема UserResponse скрывает их.
    """
    res = await db.execute(select(User).where(User.id == user_id))
    user = res.scalar_one_or_none()
    if not user:
        raise NotFoundError("User not found", "USER_NOT_FOUND")
    return user


# ---------------------------------------------------------------------------
# Soft verification & 2FA switches (опционально, без внешних интеграций)
# ---------------------------------------------------------------------------


class VerificationToggle(BaseModel):
    value: bool


@router.post("/me/verify/email", response_model=SuccessResponse)
async def toggle_email_verified(
    body: VerificationToggle,
    current_user: User = Depends(get_current_verified_user),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Сервисный переключатель флага подтверждения email (для MVP/админ-панели).
    В проде подтверждение идёт через код/ссылку — здесь только флаг с аудитом.
    """
    db_user = await db.get(User, current_user.id)
    if not db_user:
        raise AuthenticationError("User not found", "USER_NOT_FOUND")

    if not getattr(db_user, "email", None):
        raise SmartSellValidationError("No email attached to account", "EMAIL_MISSING")

    old = getattr(db_user, "is_email_verified", False)
    setattr(db_user, "is_email_verified", bool(body.value))
    await db.commit()

    audit_logger.log_data_change(
        user_id=db_user.id,
        action="verify_email_toggle",
        resource_type="user",
        resource_id=str(db_user.id),
        changes={"is_email_verified": {"old": old, "new": bool(body.value)}},
    )
    return SuccessResponse(message="Email verification flag updated", data={"is_email_verified": bool(body.value)})


@router.post("/me/verify/phone", response_model=SuccessResponse)
async def toggle_phone_verified(
    body: VerificationToggle,
    current_user: User = Depends(get_current_verified_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Переключатель флага подтверждения телефона (служебно)."""
    db_user = await db.get(User, current_user.id)
    if not db_user:
        raise AuthenticationError("User not found", "USER_NOT_FOUND")

    if not getattr(db_user, "phone", None):
        raise SmartSellValidationError("No phone attached to account", "PHONE_MISSING")

    old = getattr(db_user, "is_verified", False)
    setattr(db_user, "is_verified", bool(body.value))
    await db.commit()

    audit_logger.log_data_change(
        user_id=db_user.id,
        action="verify_phone_toggle",
        resource_type="user",
        resource_id=str(db_user.id),
        changes={"is_verified": {"old": old, "new": bool(body.value)}},
    )
    return SuccessResponse(message="Phone verification flag updated", data={"is_verified": bool(body.value)})


@router.post("/me/2fa", response_model=SuccessResponse)
async def toggle_2fa(
    body: VerificationToggle,
    current_user: User = Depends(get_current_verified_user),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Переключатель 2FA-флага на аккаунте (без генерации секретов/QR — это в отдельном модуле).
    """
    db_user = await db.get(User, current_user.id)
    if not db_user:
        raise AuthenticationError("User not found", "USER_NOT_FOUND")

    old = getattr(db_user, "is_two_factor_enabled", False)
    setattr(db_user, "is_two_factor_enabled", bool(body.value))
    await db.commit()

    audit_logger.log_data_change(
        user_id=db_user.id,
        action="2fa_toggle",
        resource_type="user",
        resource_id=str(db_user.id),
        changes={"is_two_factor_enabled": {"old": old, "new": bool(body.value)}},
    )
    return SuccessResponse(message="2FA setting updated", data={"is_two_factor_enabled": bool(body.value)})
