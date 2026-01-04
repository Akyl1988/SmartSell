"""
User management endpoints (enterprise-ready).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, TypeVar

from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_async_db
from app.core.dependencies import (
    Pagination,
    api_rate_limit,
    get_current_user,
    get_current_verified_user,
    get_pagination,
)
from app.core.exceptions import AuthenticationError, NotFoundError, SmartSellValidationError
from app.core.logging import audit_logger
from app.core.security import get_password_hash, verify_password
from app.models.user import User, UserSession
from app.schemas.base import PaginatedResponse, SuccessResponse
from app.schemas.user import UserResponse, UserUpdate

router = APIRouter(prefix="/users", tags=["Users"], dependencies=[Depends(api_rate_limit)])

T = TypeVar("T")


async def _run_sync(db: AsyncSession, fn: Callable[[Any], T]) -> T:
    return await db.run_sync(fn)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _is_admin(user: User) -> bool:
    # Поддерживаем обе возможные модели флага
    return bool(getattr(user, "is_superuser", False) or getattr(user, "is_admin", False))


def _apply_user_filters(query, search: str | None, is_active: bool | None):
    if is_active is not None:
        query = query.filter(User.is_active == is_active)

    if search:
        s = f"%{search}%"
        query = query.filter(
            or_(
                User.full_name.ilike(s),
                User.phone.ilike(s),
                User.email.ilike(s),
            )
        )
    return query


_ALLOWED_SORT_FIELDS = {
    "id": User.id,
    "full_name": User.full_name,
    "phone": User.phone,
    "email": User.email,
    "created_at": getattr(User, "created_at", User.id),
    "updated_at": getattr(User, "updated_at", User.id),
    "last_login_at": getattr(User, "last_login_at", User.id),
}


def _apply_sorting(query, sort_by: str, sort_order: str):
    col = _ALLOWED_SORT_FIELDS.get(sort_by, _ALLOWED_SORT_FIELDS["created_at"])
    return query.order_by(col.asc() if sort_order.lower() == "asc" else col.desc())


# ---------------------------------------------------------------------------
# Me
# ---------------------------------------------------------------------------


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
# Public profiles & admin listing
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


@router.get("", response_model=PaginatedResponse[UserResponse])
async def list_users(
    pagination: Pagination = Depends(get_pagination),
    sort_by: str = Query("created_at"),
    sort_order: str = Query("desc", pattern="^(?i)(asc|desc)$"),
    search: str | None = Query(None),
    is_active: bool | None = Query(None),
    current_user: User = Depends(get_current_verified_user),
    db: AsyncSession = Depends(get_async_db),
):
    """
    List users with filters (admin-only if флаг админа присутствует).
    Если в модели нет флага админа — допускаем только самого пользователя (вернём 1 запись — его).
    """
    if hasattr(User, "is_superuser") or hasattr(User, "is_admin"):
        if not _is_admin(current_user):
            # Не админ — ограничим только собой
            total = 1
            items = [current_user]
            return PaginatedResponse.create(
                items=items, total=total, page=pagination.page, per_page=pagination.per_page
            )

    def _sync_fetch(session: Any):
        q = session.query(User)
        q = _apply_user_filters(q, search, is_active)
        total_local = q.order_by(None).count()
        q = _apply_sorting(q, sort_by, sort_order)
        items = q.offset(pagination.offset).limit(pagination.limit).all()
        return total_local, items

    total, users = await _run_sync(db, _sync_fetch)

    return PaginatedResponse.create(items=users, total=total, page=pagination.page, per_page=pagination.per_page)


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
