# app/routers/auth.py
from __future__ import annotations

"""
Authentication router for user registration, login, and token management (enterprise-grade).
"""

from datetime import timedelta

from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

# DB session (учитываем возможное объединение db/database)
try:
    from app.core.database import get_db  # type: ignore
except Exception:
    from app.core.db import get_db  # fallback

# Опциональные расширения безопасности (не обязательны — используем if getattr)
from app.core import security as sec
from app.core.config import settings
from app.core.deps import auth_rate_limit_dep, ensure_idempotency
from app.core.errors import bad_request, not_found, server_error, unauthorized
from app.core.logging import audit_logger
from app.core.security import (
    create_access_token,
    create_refresh_token,
    get_current_user,
    get_password_hash,
    verify_password,
    verify_token,
)
from app.models import Company, OtpAttempt, User
from app.schemas.user import (
    PasswordChange,
    PasswordReset,
    RefreshToken,
    TokenResponse,
    UserLogin,
    UserRegister,
    UserResponse,
)
from app.services.mobizon_service import MobizonService
from app.utils.otp import generate_otp_code, hash_otp_code, verify_otp_code

router = APIRouter(
    prefix="/auth",
    tags=["auth"],
)

# ---------------------------------------------------------------------
# Вспомогательные функции/константы
# ---------------------------------------------------------------------

_ACCESS_EXPIRES = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
_REFRESH_EXPIRES = timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)


def _set_refresh_cookie(resp: Response, token: str) -> None:
    """
    Опционально выставляет refresh-токен в HttpOnly cookie (двойная защита).
    Включается, если в настройках есть флаг USE_REFRESH_COOKIE=True.
    """
    if not getattr(settings, "USE_REFRESH_COOKIE", False):
        return
    resp.set_cookie(
        key="refresh_token",
        value=token,
        httponly=True,
        secure=getattr(settings, "COOKIE_SECURE", True),
        samesite=getattr(settings, "COOKIE_SAMESITE", "lax"),
        max_age=int(_REFRESH_EXPIRES.total_seconds()),
        path="/auth",
    )


async def _revoke_refresh_jti_if_supported(payload: dict) -> None:
    """
    Если в security реализована denylist для JTI — пометим JTI как отозванный.
    Иначе тихо пропускаем.
    """
    jti = payload.get("jti")
    if not jti:
        return
    revoke_fn = getattr(sec, "revoke_jti", None)
    if callable(revoke_fn):
        try:
            await revoke_fn(jti) if hasattr(revoke_fn, "__await__") else revoke_fn(jti)
        except Exception:
            # Не валим поток аутентификации
            pass


async def _validate_password_strength_if_supported(password: str) -> None:
    """Если есть строгая политика паролей — применим её."""
    validator = getattr(sec, "validate_password_strength", None)
    if callable(validator):
        msg = await validator(password) if hasattr(validator, "__await__") else validator(password)
        if msg:  # предполагаем, что возвратит None/"" если ок, иначе текст ошибки
            raise bad_request(msg)


# ---------------------------------------------------------------------
# Register
# ---------------------------------------------------------------------


@router.post(
    "/register",
    response_model=TokenResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(auth_rate_limit_dep), Depends(ensure_idempotency)],
)
async def register(
    user_data: UserRegister,
    db: AsyncSession = Depends(get_db),
    request: Request = None,
    response: Response = None,
):
    """Register new user and company (идемпотентно)."""

    # Политика паролей (если доступна)
    await _validate_password_strength_if_supported(user_data.password)

    # Проверки уникальности
    exists = await db.execute(select(User.id).where(User.phone == user_data.phone))
    if exists.scalar_one_or_none():
        raise bad_request("User with this phone already exists")

    if user_data.email:
        exists_email = await db.execute(
            select(User.id).where(and_(User.email == user_data.email, User.email.isnot(None)))
        )
        if exists_email.scalar_one_or_none():
            raise bad_request("User with this email already exists")

    # Атомарно создаём компанию и пользователя
    async with db.begin():
        company = Company(name=user_data.company_name, bin_iin=user_data.bin_iin)
        db.add(company)
        await db.flush()

        user = User(
            company_id=company.id,
            phone=user_data.phone,
            email=user_data.email,
            first_name=user_data.first_name,
            last_name=user_data.last_name,
            hashed_password=get_password_hash(user_data.password),
            role="admin",  # Первый пользователь — админ
            is_active=True,
        )
        db.add(user)

    # Генерация токенов
    access_token = create_access_token({"sub": str(user.id), "role": user.role})
    refresh_token = create_refresh_token({"sub": str(user.id)})

    # Если включено — кука с refresh
    if response is not None:
        _set_refresh_cookie(response, refresh_token)

    # Аудит
    ip = request.client.host if request else "unknown"
    ua = request.headers.get("user-agent", "unknown") if request else "unknown"
    audit_logger.log_data_change(
        user_id=user.id,
        action="user_register",
        resource_type="user",
        resource_id=str(user.id),
        changes={"company_id": company.id, "phone": user.phone},
    )
    audit_logger.log_auth_success(user_id=user.id, ip_address=ip, user_agent=ua)

    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=int(_ACCESS_EXPIRES.total_seconds()),
    )


# ---------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------


@router.post(
    "/login",
    response_model=TokenResponse,
    dependencies=[Depends(auth_rate_limit_dep)],
)
async def login(
    user_data: UserLogin,
    db: AsyncSession = Depends(get_db),
    request: Request = None,
    response: Response = None,
):
    """Login user with password or OTP."""
    ip = request.client.host if request else "unknown"
    ua = request.headers.get("user-agent", "unknown") if request else "unknown"

    result = await db.execute(select(User).where(User.phone == user_data.phone))
    user = result.scalar_one_or_none()
    if not user:
        audit_logger.log_auth_failure(
            username=user_data.phone, ip_address=ip, reason="user_not_found"
        )
        raise unauthorized("Invalid credentials")

    if not user.is_active:
        audit_logger.log_security_event(
            "user_login_blocked", {"user_id": user.id, "reason": "inactive"}
        )
        raise bad_request("User account is disabled")

    # Проверка: пароль или OTP
    if user_data.password and user.hashed_password:
        if not verify_password(user_data.password, user.hashed_password):
            audit_logger.log_auth_failure(username=user.phone, ip_address=ip, reason="bad_password")
            raise unauthorized("Invalid credentials")
    elif user_data.otp_code:
        valid_otp = await verify_otp_code(db, user_data.phone, user_data.otp_code)
        if not valid_otp:
            audit_logger.log_auth_failure(username=user.phone, ip_address=ip, reason="bad_otp")
            raise unauthorized("Invalid or expired OTP code")
    else:
        raise bad_request("Password or OTP code required")

    # Создаём токены
    access_token = create_access_token({"sub": str(user.id), "role": user.role})
    refresh_token = create_refresh_token({"sub": str(user.id)})

    if response is not None:
        _set_refresh_cookie(response, refresh_token)

    audit_logger.log_auth_success(user_id=user.id, ip_address=ip, user_agent=ua)
    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=int(_ACCESS_EXPIRES.total_seconds()),
    )


# ---------------------------------------------------------------------
# Refresh
# ---------------------------------------------------------------------


@router.post(
    "/token/refresh",
    response_model=TokenResponse,
    dependencies=[Depends(auth_rate_limit_dep)],
)
async def refresh_token(
    token_data: RefreshToken,
    db: AsyncSession = Depends(get_db),
    request: Request = None,
    response: Response = None,
):
    """Refresh access token (rotation; deny old if supported)."""

    # Если настроены куки — разрешаем брать refresh из cookie, если тело пустое
    raw_refresh = token_data.refresh_token
    if not raw_refresh and request is not None:
        raw_refresh = request.cookies.get("refresh_token")

    payload = verify_token(raw_refresh, "refresh")
    if not payload:
        raise unauthorized("Invalid refresh token")

    user_id = payload.get("sub")
    if not user_id:
        raise unauthorized("Invalid token payload")

    # Проверка пользователя
    user = (await db.execute(select(User).where(User.id == int(user_id)))).scalar_one_or_none()
    if not user or not user.is_active:
        raise unauthorized("User not found or inactive")

    # Ревокация старого refresh по JTI (если поддерживается)
    await _revoke_refresh_jti_if_supported(payload)

    # Ротация refresh
    access = create_access_token({"sub": str(user.id), "role": user.role})
    new_refresh = create_refresh_token({"sub": str(user.id)})

    if response is not None:
        _set_refresh_cookie(response, new_refresh)

    return TokenResponse(
        access_token=access,
        refresh_token=new_refresh,
        expires_in=int(_ACCESS_EXPIRES.total_seconds()),
    )


# ---------------------------------------------------------------------
# Revoke refresh (опционально реализован denylist)
# ---------------------------------------------------------------------


@router.post(
    "/token/revoke",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(auth_rate_limit_dep)],
)
async def revoke_refresh(token_data: RefreshToken, request: Request = None):
    """Явная ревокация refresh-токена (если поддерживается denylist)."""
    raw = token_data.refresh_token or (request.cookies.get("refresh_token") if request else None)
    if not raw:
        raise bad_request("Refresh token is required")

    payload = verify_token(raw, "refresh")
    if not payload:
        raise unauthorized("Invalid refresh token")

    await _revoke_refresh_jti_if_supported(payload)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------
# Send OTP
# ---------------------------------------------------------------------


@router.post(
    "/send-otp",
    dependencies=[Depends(auth_rate_limit_dep), Depends(ensure_idempotency)],
)
async def send_otp(phone: str, purpose: str = "login", db: AsyncSession = Depends(get_db)):
    """Send OTP code to phone (идемпотентно)."""
    # Сгенерировать/захешировать
    otp_code = generate_otp_code()
    otp_hash = hash_otp_code(otp_code)

    async with db.begin():
        otp_attempt = OtpAttempt.create_new(phone=phone, code_hash=otp_hash, purpose=purpose)
        db.add(otp_attempt)

    # SMS
    mobizon = MobizonService()
    ok = await mobizon.send_sms(phone=phone, message=f"Ваш код подтверждения SmartSell: {otp_code}")
    if not ok:
        raise server_error("Failed to send SMS")

    return {"message": "OTP sent successfully"}


# ---------------------------------------------------------------------
# Change password (авторизованный)
# ---------------------------------------------------------------------


@router.post(
    "/change-password",
    dependencies=[Depends(auth_rate_limit_dep)],
)
async def change_password(
    password_data: PasswordChange,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Change user password (требуется текущий пароль)."""

    if not current_user.hashed_password or not verify_password(
        password_data.current_password, current_user.hashed_password
    ):
        raise bad_request("Invalid current password")

    await _validate_password_strength_if_supported(password_data.new_password)

    async with db.begin():
        current_user.hashed_password = get_password_hash(password_data.new_password)

    audit_logger.log_security_event(
        "password_change",
        {"user_id": current_user.id, "company_id": current_user.company_id},
    )
    return {"message": "Password changed successfully"}


# ---------------------------------------------------------------------
# Reset password (OTP)
# ---------------------------------------------------------------------


@router.post(
    "/reset-password",
    dependencies=[Depends(auth_rate_limit_dep)],
)
async def reset_password(reset_data: PasswordReset, db: AsyncSession = Depends(get_db)):
    """Reset password with OTP verification."""
    ok = await verify_otp_code(db, reset_data.phone, reset_data.otp_code, "reset_password")
    if not ok:
        raise bad_request("Invalid or expired OTP code")

    user = (
        await db.execute(select(User).where(User.phone == reset_data.phone))
    ).scalar_one_or_none()
    if not user:
        raise not_found("User not found")

    await _validate_password_strength_if_supported(reset_data.new_password)

    async with db.begin():
        user.hashed_password = get_password_hash(reset_data.new_password)

    audit_logger.log_security_event(
        "password_reset",
        {"user_id": user.id, "company_id": user.company_id},
    )
    return {"message": "Password reset successfully"}


# ---------------------------------------------------------------------
# Me
# ---------------------------------------------------------------------


@router.get("/me", response_model=UserResponse)
async def get_current_user_info(current_user: User = Depends(get_current_user)):
    """Get current user information."""
    return current_user


# ---------------------------------------------------------------------
# Logout (статлесс)
# ---------------------------------------------------------------------


@router.post("/logout")
async def logout(
    current_user: User = Depends(get_current_user),
    response: Response = None,
    request: Request = None,
):
    """
    Logout user:
    - клиент должен забыть access;
    - при включённой cookie-стратегии: чистим refresh cookie;
    - при наличии denylist/JTI — можно дополнительно ревокнуть refresh из cookie.
    """
    # Чистим refresh cookie (если стратегия включена)
    if response and getattr(settings, "USE_REFRESH_COOKIE", False):
        response.delete_cookie("refresh_token", path="/auth")

    # «Best effort» ревокация refresh из cookie (если есть)
    if request and getattr(settings, "USE_REFRESH_COOKIE", False):
        rt = request.cookies.get("refresh_token")
        if rt:
            payload = verify_token(rt, "refresh")
            if payload:
                await _revoke_refresh_jti_if_supported(payload)

    audit_logger.log_data_change(
        user_id=current_user.id,
        action="user_logout",
        resource_type="user",
        resource_id=str(current_user.id),
        changes={"logout": True},
    )
    return {"message": "Logged out successfully"}
