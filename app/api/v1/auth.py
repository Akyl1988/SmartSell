# app/api/v1/auth.py
"""
Authentication endpoints for user registration, login, token refresh/rotation,
logout, OTP, and small health checks — production-grade.

Ключевые особенности:
- Возврат токенов при регистрации (совместимо с тестами): по умолчанию включено,
  можно отключить через ENV AUTH_REGISTER_ISSUE_TOKENS=0.
- Безопасная работа со временем (UTC), троттлинг OTP, переиспользование активной OTP (cooldown).
- Сессии refresh-токенов храним в БД в виде SHA-256 от значения, поддержка logout/refresh.
- Централизованный слой для SMS-провайдеров (Mobizon и пр.) — graceful degradation.
- Подробное аудит-логирование (успехи/ошибки/системные события).
- Корректные коды ошибок и сообщения (через кастомные исключения).

⚙️ Обновления:
- Полный переход на SQLAlchemy AsyncSession: `select(...) + await db.execute(...)`,
  `await db.commit() / await db.refresh()`. Никаких `.query(...)` (которого нет у AsyncSession).
- Безопасные массовые обновления через `update(...)` (reset-password).
"""

from __future__ import annotations

import hashlib
import os
import re
import secrets
import time
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import noload

from app.core.config import settings
from app.core.db import get_async_db
from app.core.dependencies import (
    auth_rate_limit,
    enforce_rate_limit,
    get_client_info,
    get_current_user,
    get_otp_service,
    otp_rate_limit,
)
from app.core.exceptions import (
    AuthenticationError,
    AuthorizationError,
    ConflictError,
    ExternalServiceError,
    SmartSellValidationError,
)
from app.core.logging import audit_logger, get_logger
from app.core.rbac import is_superuser
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_and_validate,
    denylist_key_for_token,
    get_password_hash,
    resolve_tenant_company_id,
    revoke_token,
    validate_password_policy,
    verify_password,
)
from app.integrations.errors import ProviderNotConfiguredError
from app.integrations.ports.otp import OtpProvider
from app.models.company import Company
from app.models.invitation import InvitationToken, PasswordResetToken
from app.models.otp import OtpAttempt  # таблица otp_codes и enterprise-OTP попытки
from app.models.user import User, UserSession
from app.schemas.base import SuccessResponse
from app.schemas.user import (
    InvitationAccept,
    InvitationCreate,
    OTPRequest,
    OTPVerify,
    PasswordReset,
    PasswordResetConfirm,
    PasswordResetRequest,
    PhoneChangeConfirm,
    PhoneChangeRequest,
    RefreshTokenRequest,
    TokenResponse,
    UserCreate,
    UserLogin,
)
from app.services.messaging import MessagingConfigError, send_email
from app.services.otp_providers import is_otp_active, require_otp_provider_or_admin_bypass
from app.utils.otp import create_otp_attempt, verify_otp_code
from app.utils.tokens import generate_token, hash_token

router = APIRouter(prefix="/auth", tags=["Authentication"])
logger = get_logger(__name__)
bearer_optional = HTTPBearer(auto_error=False)

# =============================================================================
# Конфигурация/политики (с дефолтами)
# =============================================================================


def _conf(name: str, default):
    return getattr(settings, name, default)


OTP_CODE_LEN: int = int(_conf("OTP_CODE_LEN", 6))
OTP_TTL_MINUTES: int = int(_conf("OTP_TTL_MINUTES", 10))
OTP_MAX_ATTEMPTS: int = int(_conf("OTP_MAX_ATTEMPTS", 3))
OTP_RESEND_COOLDOWN_SEC: int = int(_conf("OTP_RESEND_COOLDOWN_SEC", 60))
LOGIN_MAX_FAILS: int = int(_conf("LOGIN_MAX_FAILS", 5))
LOGIN_LOCK_MINUTES: int = int(_conf("LOGIN_LOCK_MINUTES", 15))
PROJECT_NAME: str = str(_conf("PROJECT_NAME", "SmartSell"))
DEBUG_MODE: bool = bool(_conf("DEBUG", False))
DEBUG_OTP_CODE: str | None = getattr(settings, "DEBUG_OTP_CODE", None)
DEBUG_OTP_LOGGING: bool = bool(_conf("DEBUG_OTP_LOGGING", False))
AUTH_REGISTER_ISSUE_TOKENS: bool = str(_conf("AUTH_REGISTER_ISSUE_TOKENS", "1")) in (
    "1",
    "true",
    "True",
)

# Цели OTP, по которым подтверждаем учётку
OTP_PURPOSE_VERIFY_FLAGS = {"registration", "register", "verify"}

# =============================================================================
# Вспомогалки
# =============================================================================


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _utcnow_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _normalize_phone(v: str | None) -> str:
    # Normalize to digits only to compare consistently across stored formats
    return re.sub(r"\D", "", (v or "").strip())


def _phone_variants(phone: str) -> list[str]:
    """Return common representations to match stored values."""
    digits = _normalize_phone(phone)
    variants = []
    if digits:
        variants.append(digits)
        plus_form = f"+{digits}"
        if plus_form not in variants:
            variants.append(plus_form)
    raw = (phone or "").strip()
    if raw and raw not in variants:
        variants.append(raw)
    return variants


def _normalize_email(v: str | None) -> str | None:
    vv = (v or "").strip()
    return vv or None


def _looks_like_email(value: str) -> bool:
    v = (value or "").strip()
    return bool(re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", v))


def _normalize_purpose(v: str | None) -> str:
    return (v or "").strip().lower() or "login"


def _env_int(name: str, default: int) -> int:
    try:
        raw = os.getenv(name)
        return int(raw) if raw is not None else int(default)
    except Exception:
        return int(default)


def _enforce_password_policy(password: str, *, username: str | None = None, email: str | None = None) -> None:
    ok, errors = validate_password_policy(password, username=username, email=email)
    if ok:
        return
    raise SmartSellValidationError(
        "password_policy_violation",
        "password_policy_violation",
        http_status=400,
        extra={"errors": errors},
    )


async def _enforce_otp_phone_rate_limit(phone: str) -> None:
    if not phone:
        return
    await enforce_rate_limit(
        tag="otp_phone",
        ident=f"otp:phone:{phone}",
        max_requests=_env_int("OTP_PHONE_RATE_LIMIT", 5),
        window_seconds=_env_int("OTP_PHONE_RATE_WINDOW", 60),
        detail="otp_phone_rate_limited",
    )


async def _enforce_login_identifier_rate_limit(identifier: str) -> None:
    if not identifier:
        return
    await enforce_rate_limit(
        tag="login_ident",
        ident=f"login:identifier:{identifier}",
        max_requests=_env_int("LOGIN_IDENTIFIER_RATE_LIMIT", 10),
        window_seconds=_env_int("LOGIN_IDENTIFIER_RATE_WINDOW", 60),
        detail="login_rate_limited",
    )


async def _enforce_refresh_rate_limit(ident: str) -> None:
    if not ident:
        return
    await enforce_rate_limit(
        tag="refresh",
        ident=ident,
        max_requests=_env_int("REFRESH_RATE_LIMIT", 20),
        window_seconds=_env_int("REFRESH_RATE_WINDOW", 60),
        detail="auth_refresh_rate_limited",
    )


async def _enforce_logout_rate_limit(ident: str) -> None:
    if not ident:
        return
    await enforce_rate_limit(
        tag="logout",
        ident=ident,
        max_requests=_env_int("LOGOUT_RATE_LIMIT", 30),
        window_seconds=_env_int("LOGOUT_RATE_WINDOW", 60),
        detail="auth_logout_rate_limited",
    )


def _gen_otp_code(length: int = OTP_CODE_LEN) -> str:
    return "".join(str(secrets.randbelow(10)) for _ in range(max(4, min(10, length))))


def _issue_tokens_for_user(user_id: int) -> tuple[str, str]:
    return create_access_token(subject=user_id), create_refresh_token(subject=user_id)


def _hash_refresh_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _sms_text_for_otp(code: str, purpose: str) -> str:
    p = purpose or "login"
    return f"{PROJECT_NAME}: код подтверждения {code} для {p}. Никому не сообщайте."


def _mask_phone(value: str) -> str:
    digits = re.sub(r"\D", "", value or "")
    if not digits:
        return ""
    if len(digits) <= 4:
        return "*" * len(digits)
    return "*" * (len(digits) - 2) + digits[-2:]


def _should_return_provider_info() -> bool:
    env = str(os.getenv("ENVIRONMENT") or getattr(settings, "ENVIRONMENT", "production") or "production").lower()
    debug_flag = os.getenv("DEBUG_PROVIDER_INFO", "").strip()
    if debug_flag.lower() in {"1", "true", "yes", "on"}:
        return True
    if env == "production":
        return False
    is_test_env = bool(os.getenv("PYTEST_CURRENT_TEST")) or bool(getattr(settings, "TESTING", False))
    return is_test_env or (env != "production")


def _is_production() -> bool:
    env = str(os.getenv("ENVIRONMENT") or getattr(settings, "ENVIRONMENT", "production") or "production").lower()
    return env == "production"


def _public_url() -> str:
    return str(getattr(settings, "PUBLIC_URL", os.getenv("PUBLIC_URL", "http://localhost:8000")) or "").rstrip("/")


# =============================================================================
# SMS provider (ленивая инициализация)
# =============================================================================

_SMS_CLIENT = None


def _get_sms_client_or_none():
    """
    Ожидается реализация через app.integrations.sms_base.get_sms_client().
    Если провайдер не сконфигурирован — возвращаем None (без падений).
    """
    global _SMS_CLIENT
    if _SMS_CLIENT is not None:
        return _SMS_CLIENT
    try:
        from app.integrations.sms_base import get_sms_client  # type: ignore

        _SMS_CLIENT = get_sms_client()
        return _SMS_CLIENT
    except Exception:
        return None


def _send_otp_via_provider(phone: str, text: str) -> dict | None:
    client = _get_sms_client_or_none()
    if client is None:
        return None
    try:
        return client.send_sms(recipient=phone, text=text)
    except Exception as e:
        audit_logger.log_system_event(
            level="warning",
            event="sms_send_failed",
            message=str(e),
            meta={"phone": phone},
        )
        return {"success": False, "error": str(e)}


# =============================================================================
# Health/debug
# =============================================================================


@router.get("/health", response_model=SuccessResponse)
async def health():
    provider = None
    try:
        from app.integrations.sms_base import get_sms_client  # type: ignore

        provider = type(get_sms_client()).__name__
    except Exception:
        provider = "unconfigured"
    return SuccessResponse(
        message="auth ok",
        data={
            "provider": provider,
            "otp_ttl_minutes": OTP_TTL_MINUTES,
            "otp_max_attempts": OTP_MAX_ATTEMPTS,
            "register_returns_tokens": AUTH_REGISTER_ISSUE_TOKENS,
        },
    )


# =============================================================================
# DAL helpers (минимальные, чтобы не дублировать select)
# =============================================================================


async def _get_user_by_phone(db: AsyncSession, phone: str) -> User | None:
    variants = _phone_variants(phone)
    res = await db.execute(select(User).where(User.phone.in_(variants)))
    return res.scalars().first()


async def _get_user_by_email(db: AsyncSession, email: str) -> User | None:
    res = await db.execute(select(User).where(User.email == email))
    return res.scalars().first()


# =============================================================================
# Registration
# =============================================================================


@router.post(
    "/register",
    response_model=TokenResponse if AUTH_REGISTER_ISSUE_TOKENS else SuccessResponse,
    dependencies=[Depends(auth_rate_limit)],
)
async def register(user_data: UserCreate, request: Request, db: AsyncSession = Depends(get_async_db)):
    """
    Регистрация пользователя.
    По умолчанию сразу возвращаем токены (для UX/тестов). Можно отключить через AUTH_REGISTER_ISSUE_TOKENS=0.
    """
    client_info = get_client_info(request)

    require_otp_provider_or_admin_bypass(None, action="register")

    phone = _normalize_phone(user_data.phone)
    email = _normalize_email(user_data.email)

    # Уникальность по телефону
    existing_user = await _get_user_by_phone(db, phone)
    if existing_user:
        audit_logger.log_auth_failure(
            username=phone,
            ip_address=client_info["ip_address"],
            reason="User already exists",
        )
        raise ConflictError("User with this phone number already exists", "USER_EXISTS")

    # Уникальность по email (если задан)
    if email:
        existing_email = await _get_user_by_email(db, email)
        if existing_email:
            raise ConflictError("User with this email already exists", "EMAIL_EXISTS")

    try:
        _enforce_password_policy(user_data.password, username=phone or None, email=email)
        hashed_password = get_password_hash(user_data.password)

        # Create draft Company (tenant) for the new user
        company_name = (user_data.company_name or "").strip()
        if not company_name:
            # Use phone as fallback for company name (normalized, human-readable)
            company_name = f"Draft {phone}"

        company = Company(
            name=company_name,
            is_active=True,
            subscription_plan="start",
        )
        db.add(company)
        await db.flush()  # Get company.id without committing

        # Create user and bind to company
        user = User(
            phone=phone,
            email=email,
            full_name=user_data.full_name,
            hashed_password=hashed_password,
            is_active=True,
            is_verified=False,
            company_id=company.id,
        )
        db.add(user)
        await db.flush()  # Get user.id without committing

        # Set company owner to the new user
        company.owner_id = user.id
        await db.commit()
        await db.refresh(user)
        await db.refresh(company)

        audit_logger.log_data_change(
            user_id=user.id,
            action="create",
            resource_type="user",
            resource_id=str(user.id),
            changes={"phone": phone, "email": email, "company_id": company.id},
        )

        if not AUTH_REGISTER_ISSUE_TOKENS:
            return SuccessResponse(
                message="User registered successfully. Please verify your phone number.",
                data={"user_id": user.id, "company_id": company.id},
            )

        access_token, refresh_token = _issue_tokens_for_user(user.id)
        session = UserSession(
            user_id=user.id,
            refresh_token=_hash_refresh_token(refresh_token),
            ip_address=client_info["ip_address"],
            user_agent=client_info["user_agent"],
            expires_at=_utcnow_naive() + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS),
            is_active=True,
        )
        db.add(session)
        user.last_login_at = _utcnow_naive()
        await db.commit()

        audit_logger.log_auth_success(
            user_id=user.id,
            ip_address=client_info["ip_address"],
            user_agent=client_info["user_agent"],
        )

        return TokenResponse(
            access_token=access_token,
            refresh_token=refresh_token,
            token_type="bearer",
            expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        )

    except IntegrityError as e:
        await db.rollback()
        msg = str(getattr(e, "orig", e))
        if "phone" in msg:
            raise ConflictError("Phone number already registered", "DUPLICATE_PHONE")
        if "email" in msg:
            raise ConflictError("Email already registered", "DUPLICATE_EMAIL")
        raise ConflictError("Registration failed due to data conflict", "REGISTRATION_FAILED")


# =============================================================================
# Login / Refresh / Logout
# =============================================================================


@router.post(
    "/login",
    response_model=TokenResponse,
    dependencies=[Depends(auth_rate_limit)],
)
async def login(login_data: UserLogin, request: Request, db: AsyncSession = Depends(get_async_db)):
    """Аутентификация по телефону и паролю. Возвращает access/refresh токены."""
    client_info = get_client_info(request)
    identifier = (login_data.identifier or "").strip()
    is_email = _looks_like_email(identifier)
    phone = _normalize_phone(identifier) if not is_email else ""
    email = identifier.lower() if is_email else ""
    otp_code = (login_data.otp_code or "").strip()
    via_otp = bool(otp_code)

    identifier_norm = email or phone or identifier.lower()
    await _enforce_login_identifier_rate_limit(identifier_norm)

    user = await (_get_user_by_email(db, email) if is_email else _get_user_by_phone(db, phone))

    # Блокировка по неудачным попыткам
    if user and user.locked_until and user.locked_until > _utcnow_naive():
        audit_logger.log_auth_failure(
            username=identifier,
            ip_address=client_info["ip_address"],
            reason="Account locked",
        )
        raise AuthenticationError("Account is temporarily locked", "ACCOUNT_LOCKED")

    if via_otp:
        if is_email:
            raise AuthenticationError("Invalid OTP code", "INVALID_OTP")
        if not user:
            raise AuthenticationError("Invalid OTP code", "INVALID_OTP")

        verified = await verify_otp_code(db, phone, otp_code, "login")
        if not verified:
            raise AuthenticationError("Invalid OTP code", "INVALID_OTP")
    else:
        password = login_data.password or ""
        if not user or not verify_password(password, user.hashed_password):
            if user:
                user.failed_login_attempts = (user.failed_login_attempts or 0) + 1
                if user.failed_login_attempts >= LOGIN_MAX_FAILS:
                    user.locked_until = _utcnow_naive() + timedelta(minutes=LOGIN_LOCK_MINUTES)
                await db.commit()

            audit_logger.log_auth_failure(
                username=identifier,
                ip_address=client_info["ip_address"],
                reason="Invalid credentials",
            )
            raise AuthenticationError("Invalid credentials", "INVALID_CREDENTIALS")

    if not user.is_active:
        audit_logger.log_auth_failure(
            username=identifier,
            ip_address=client_info["ip_address"],
            reason="Inactive account",
        )
        raise AuthenticationError("Account is inactive", "INACTIVE_ACCOUNT")

    # Success
    access_token, refresh_token = _issue_tokens_for_user(user.id)

    session = UserSession(
        user_id=user.id,
        refresh_token=_hash_refresh_token(refresh_token),
        ip_address=client_info["ip_address"],
        user_agent=client_info["user_agent"],
        expires_at=_utcnow_naive() + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS),
        is_active=True,
    )
    db.add(session)

    user.last_login_at = _utcnow_naive()
    user.failed_login_attempts = 0
    user.locked_until = None

    await db.commit()

    audit_logger.log_auth_success(
        user_id=user.id,
        ip_address=client_info["ip_address"],
        user_agent=client_info["user_agent"],
    )

    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        token_type="bearer",
        expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )


@router.post("/refresh", response_model=TokenResponse)
async def refresh_token(
    refresh_data: RefreshTokenRequest,
    request: Request,
    db: AsyncSession = Depends(get_async_db),
):
    """
    Обновляет access-токен по действующему refresh-токену.
    Refresh при этом ротируется (one-time use).
    """
    client_info = get_client_info(request)
    raw_refresh = (refresh_data.refresh_token or "").strip()
    if not raw_refresh:
        await _enforce_refresh_rate_limit(client_info["ip_address"])
        raise AuthenticationError("refresh_invalid", "INVALID_REFRESH_TOKEN")

    token_hash = _hash_refresh_token(raw_refresh)
    await _enforce_refresh_rate_limit(f"refresh:token:{token_hash}")
    now = _utcnow_naive()

    res = await db.execute(
        select(UserSession)
        .options(noload(UserSession.user))
        .where(UserSession.refresh_token == token_hash)
        .with_for_update()
    )
    session = res.scalars().first()

    if not session:
        await _enforce_refresh_rate_limit(client_info["ip_address"])
        raise AuthenticationError("refresh_invalid", "INVALID_REFRESH_TOKEN")

    session_id = getattr(session, "id", None)
    ident = f"refresh:session:{session_id}" if session_id is not None else f"refresh:user:{session.user_id}"
    await _enforce_refresh_rate_limit(ident)

    if not session.is_active:
        await db.execute(
            update(UserSession)
            .where(UserSession.user_id == session.user_id, UserSession.is_active.is_(True))
            .values(is_active=False, terminated_at=now)
        )
        await db.commit()
        raise AuthenticationError("session_terminated", "SESSION_TERMINATED")

    if session.expires_at <= now:
        session.is_active = False
        session.terminated_at = now
        await db.commit()
        raise AuthenticationError("refresh_invalid", "INVALID_REFRESH_TOKEN")

    res_u = await db.execute(select(User).where(User.id == session.user_id, User.is_active.is_(True)))
    user = res_u.scalars().first()
    if not user:
        raise AuthenticationError("refresh_invalid", "INVALID_REFRESH_TOKEN")

    access_token, new_refresh_token = _issue_tokens_for_user(user.id)
    new_session = UserSession(
        user_id=user.id,
        refresh_token=_hash_refresh_token(new_refresh_token),
        ip_address=session.ip_address,
        user_agent=session.user_agent,
        expires_at=now + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS),
        is_active=True,
    )
    session.is_active = False
    session.terminated_at = now
    db.add(new_session)
    await db.commit()

    return TokenResponse(
        access_token=access_token,
        refresh_token=new_refresh_token,
        token_type="bearer",
        expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )


@router.post("/token/refresh", response_model=TokenResponse)
async def refresh_token_alias(
    refresh_data: RefreshTokenRequest,
    request: Request,
    db: AsyncSession = Depends(get_async_db),
):
    """Legacy alias for /auth/refresh used by tests/older clients."""
    return await refresh_token(refresh_data, request, db)


@router.post("/logout", response_model=SuccessResponse)
async def logout(
    request: Request,
    creds: HTTPAuthorizationCredentials | None = Depends(bearer_optional),
    refresh_data: RefreshTokenRequest | None = None,
    db: AsyncSession = Depends(get_async_db),
):
    """
    Выход из системы: деактивирует сессию по refresh-токену или отзывает текущий access-токен.
    """
    token = creds.credentials if creds else None
    if not token:
        header_val = request.headers.get("Authorization", "")
        if header_val.lower().startswith("bearer "):
            token = header_val.split(" ", 1)[1].strip()
    if not token:
        token = request.cookies.get("access_token")
    client_info = get_client_info(request)
    payload = None
    user_id = 0
    token_invalid = False
    rate_limit_applied = False
    if token:
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        await _enforce_logout_rate_limit(f"logout:token:{token_hash}")
        rate_limit_applied = True
        try:
            payload = decode_and_validate(token, expected_type="access")
            try:
                user_id = int(payload.get("sub", 0))
            except Exception:
                user_id = 0

            key = denylist_key_for_token(token, payload)
            exp = payload.get("exp")
            if key:
                ttl_seconds = None
                if exp is not None:
                    try:
                        ttl_seconds = max(1, int(float(exp) - time.time()))
                    except Exception:
                        ttl_seconds = None
                revoke_token(key, ttl_seconds=ttl_seconds)
        except Exception:
            token_invalid = True
            token = None

    if refresh_data and refresh_data.refresh_token:
        raw_refresh = (refresh_data.refresh_token or "").strip()
        token_hash = _hash_refresh_token(raw_refresh) if raw_refresh else ""
        res = await db.execute(
            select(UserSession).where(
                UserSession.refresh_token == token_hash,
                UserSession.is_active.is_(True),
            )
        )
        session = res.scalars().first()

        if session:
            await _enforce_logout_rate_limit(f"user:{session.user_id}")
        else:
            await _enforce_logout_rate_limit(
                f"logout:refresh:{token_hash}" if token_hash else client_info["ip_address"]
            )

        if session:
            session.is_active = False
            session.terminated_at = _utcnow_naive()
            await db.commit()

        return SuccessResponse(message="Logged out successfully")

    if not token:
        if not rate_limit_applied:
            await _enforce_logout_rate_limit(client_info["ip_address"])
        if token_invalid:
            return SuccessResponse(message="Logged out successfully")
        raise AuthenticationError("Authentication required", "AUTH_REQUIRED")

    if user_id > 0:
        await db.execute(
            update(UserSession)
            .where(UserSession.user_id == user_id, UserSession.is_active.is_(True))
            .values(is_active=False, terminated_at=_utcnow_naive())
        )
        await db.commit()

    return SuccessResponse(message="Logged out successfully")


class ChangePasswordPayload(BaseModel):
    current_password: str = Field(..., min_length=6)
    new_password: str = Field(..., min_length=8)


@router.post("/change-password", response_model=SuccessResponse)
async def change_password(
    payload: ChangePasswordPayload,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Change password for current user and revoke active refresh sessions."""
    if not verify_password(payload.current_password, current_user.hashed_password):
        raise AuthenticationError("Current password is incorrect", "INVALID_OLD_PASSWORD")

    if payload.current_password == payload.new_password:
        raise SmartSellValidationError("New password must be different", "PASSWORD_SAME")

    _enforce_password_policy(payload.new_password, username=current_user.phone, email=current_user.email)
    new_hash = get_password_hash(payload.new_password)

    # Persist password change + reset counters in a single UPDATE to avoid stale identity issues
    await db.execute(
        update(User)
        .where(User.id == current_user.id)
        .values(
            hashed_password=new_hash,
            failed_login_attempts=0,
            locked_until=None,
        )
    )

    await db.execute(update(UserSession).where(UserSession.user_id == current_user.id).values(is_active=False))
    await db.commit()

    audit_logger.log_data_change(
        user_id=current_user.id,
        action="change_password",
        resource_type="user",
        resource_id=str(current_user.id),
        changes={"password": "***"},
    )

    return SuccessResponse(message="Password changed successfully")


# =============================================================================
# OTP (request / verify)
# =============================================================================


@router.post(
    "/request-otp",
    response_model=SuccessResponse,
    dependencies=[Depends(auth_rate_limit), Depends(otp_rate_limit)],
)
async def request_otp(
    otp_request: OTPRequest,
    db: AsyncSession = Depends(get_async_db),
    otp_service: OtpProvider = Depends(get_otp_service),
):
    """
    Запросить OTP код для номера телефона.
    Поведение:
    - Если активная OTP ещё не истекла — переиспользуем (cooldown), не раскрывая код в ответе.
    - При DEBUG=True можно переопределить код через settings.DEBUG_OTP_CODE.
    """
    phone = _normalize_phone(otp_request.phone)
    purpose = _normalize_purpose(otp_request.purpose)

    await _enforce_otp_phone_rate_limit(phone)

    now = _utcnow_naive()
    ttl = timedelta(minutes=OTP_TTL_MINUTES)

    res = await db.execute(
        select(OtpAttempt)
        .where(
            OtpAttempt.phone.in_(_phone_variants(phone)),
            OtpAttempt.purpose == purpose,
            OtpAttempt.expires_at > now,
            OtpAttempt.is_verified.is_(False),
            OtpAttempt.deleted_at.is_(None),
        )
        .order_by(OtpAttempt.created_at.desc())
        .limit(1)
    )
    existing = res.scalars().first()

    if existing and existing.is_valid():
        created_at = getattr(existing, "created_at", None)
        age = (now - created_at).total_seconds() if isinstance(created_at, datetime) else 0.0
        if age < OTP_RESEND_COOLDOWN_SEC:
            return SuccessResponse(
                message="OTP already sent recently",
                data={"expires_in": existing.seconds_left},
            )

    code_override = str(DEBUG_OTP_CODE) if (DEBUG_MODE and DEBUG_OTP_CODE) else None

    user_for_otp = await _get_user_by_phone(db, phone)

    try:
        code, otp_attempt = await create_otp_attempt(
            db,
            phone=phone,
            purpose=purpose,
            expires_minutes=OTP_TTL_MINUTES,
            attempts_left=OTP_MAX_ATTEMPTS,
            code=code_override,
            user_id=user_for_otp.id if user_for_otp else None,
        )
    except IntegrityError:
        await db.rollback()
        raise SmartSellValidationError("Failed to create OTP", "OTP_CREATE_FAILED")

    text = _sms_text_for_otp(code, purpose)
    send_result: dict[str, Any] | None = None
    provider_name: str | None = None
    provider_version: int | None = None

    try:
        send_result = await otp_service.send_otp(
            phone=phone,
            code=code,
            ttl_seconds=int(ttl.total_seconds()),
            metadata={"purpose": purpose, "text": text},
        )
        provider_name = (send_result or {}).get("provider") or getattr(otp_service, "name", None)
        provider_version = (send_result or {}).get("version") or getattr(otp_service, "version", None)
    except ProviderNotConfiguredError as exc:
        raise HTTPException(status_code=503, detail=exc.code)
    except Exception as exc:
        provider_name = getattr(otp_service, "name", None) or "noop"
        provider_version = getattr(otp_service, "version", None)
        audit_logger.log_system_event(
            level="warning",
            event="otp_send_failed",
            message=str(exc),
            meta={"phone": phone, "purpose": purpose, "provider": provider_name},
        )

    audit_logger.log_system_event(
        level="info",
        event="otp_requested",
        message="OTP issued",
        meta={
            "phone": phone,
            "purpose": purpose,
            "provider": provider_name or (send_result or {}).get("provider", "none"),
            "provider_version": provider_version,
        },
    )

    env = str(os.getenv("ENVIRONMENT") or getattr(settings, "ENVIRONMENT", "production") or "production").lower()
    if DEBUG_OTP_LOGGING and env == "development":
        logger.info("OTP issued for phone=%s purpose=%s", _mask_phone(phone), purpose)

    data = {
        "expires_in": otp_attempt.seconds_left,
        "provider_success": (send_result or {}).get("success") if send_result else None,
        "provider_status": (send_result or {}).get("status") if send_result else None,
    }

    if _should_return_provider_info():
        data["provider"] = provider_name
        data["provider_version"] = provider_version

    env = str(os.getenv("ENVIRONMENT") or getattr(settings, "ENVIRONMENT", "production") or "production").lower()
    if env != "production" and (provider_name or "noop").startswith("noop"):
        data["dev_code"] = code

    return SuccessResponse(message="OTP code sent successfully", data=data)


@router.post(
    "/verify-otp",
    response_model=SuccessResponse,
    dependencies=[Depends(auth_rate_limit)],
)
async def verify_otp(otp_verify: OTPVerify, db: AsyncSession = Depends(get_async_db)):
    """Проверка OTP кода."""
    raw_phone = otp_verify.phone or ""
    phone = _normalize_phone(raw_phone)
    purpose = _normalize_purpose(otp_verify.purpose)

    verified = await verify_otp_code(db, phone, otp_verify.code or "", purpose)

    if not verified:
        raise SmartSellValidationError("Invalid or expired OTP code", "INVALID_OTP")

    if purpose in OTP_PURPOSE_VERIFY_FLAGS:
        variants = _phone_variants(raw_phone)
        user = await _get_user_by_phone(db, raw_phone)
        if not user and variants:
            res_user = await db.execute(select(User).where(User.phone.in_(variants)).limit(1))
            user = res_user.scalars().first()
        if not user and variants:
            res_attempt = await db.execute(
                select(OtpAttempt)
                .where(OtpAttempt.phone.in_(variants), OtpAttempt.purpose == purpose)
                .order_by(OtpAttempt.created_at.desc())
                .limit(1)
            )
            attempt = res_attempt.scalars().first()
            if attempt and attempt.user_id:
                user = await db.get(User, attempt.user_id)
        if user:
            user.is_verified = True
        updated_users = 0
        if variants:
            res_update = await db.execute(update(User).where(User.phone.in_(variants)).values(is_verified=True))
            updated_users = res_update.rowcount or 0
        await db.commit()
        audit_logger.log_system_event(
            level="info",
            event="otp_verify_mark_user",
            message="Marked user as verified",
            meta={"phone": raw_phone, "updated": updated_users, "user_found": bool(user)},
        )

    audit_logger.log_system_event(
        level="info",
        event="otp_verified",
        message="OTP verified",
        meta={"phone": phone, "purpose": purpose},
    )

    return SuccessResponse(message="OTP verified successfully")


# =============================================================================
# Invitations
# =============================================================================


@router.post(
    "/invitations",
    response_model=SuccessResponse,
    dependencies=[Depends(auth_rate_limit)],
)
async def create_invitation(
    payload: InvitationCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    company_id = resolve_tenant_company_id(current_user)
    res_company = await db.execute(select(Company).where(Company.id == company_id))
    company = res_company.scalars().first()
    is_owner = bool(company and company.owner_id and company.owner_id == current_user.id)
    from app.core.rbac import Role, is_store_admin, normalize_role

    is_admin = is_store_admin(current_user)

    if is_otp_active():
        if not is_admin:
            raise AuthorizationError("Insufficient permissions", "FORBIDDEN")
    else:
        require_otp_provider_or_admin_bypass(
            current_user,
            action="invite_create",
            company_id=company_id,
            owner_id=company.owner_id if company else None,
        )
    email = (payload.email or "").strip().lower()
    phone = _normalize_phone(payload.phone)
    variants = _phone_variants(phone)
    role = normalize_role(payload.role or Role.STORE_EMPLOYEE.value)
    if role not in {Role.STORE_ADMIN.value, Role.STORE_EMPLOYEE.value}:
        raise SmartSellValidationError("Invalid role", "INVALID_ROLE")
    if not is_owner and role == Role.STORE_ADMIN.value:
        raise AuthorizationError("Only owner can invite admins", "OWNER_REQUIRED")

    # Ensure no existing user for this company (phone/email uniqueness assumed global)
    res = await db.execute(select(User).where(User.phone.in_(variants)))
    if res.scalars().first():
        raise SmartSellValidationError("User with this phone already exists", "USER_EXISTS")
    if email:
        res_e = await db.execute(select(User).where(User.email == email))
        if res_e.scalars().first():
            raise SmartSellValidationError("User with this email already exists", "USER_EXISTS")

    token = generate_token()
    token_hash = hash_token(token, secret=getattr(settings, "INVITE_TOKEN_SECRET", None))
    invite = InvitationToken.build(
        company_id=company_id,
        role=role,
        phone=phone,
        email=email,
        display_name=payload.display_name,
        token_hash=token_hash,
        ttl_hours=72,
        created_by_user_id=current_user.id,
    )
    db.add(invite)
    await db.commit()
    await db.refresh(invite)

    data = {"invitation_id": invite.id, "expires_at": invite.expires_at}

    return SuccessResponse(message="Invitation created", data=data)


@router.post(
    "/invitations/accept",
    response_model=TokenResponse,
    dependencies=[Depends(auth_rate_limit)],
)
async def accept_invitation(payload: InvitationAccept, db: AsyncSession = Depends(get_async_db)):
    token_hash = hash_token(payload.token, secret=getattr(settings, "INVITE_TOKEN_SECRET", None))
    async with db.begin():
        res = await db.execute(
            select(InvitationToken).where(InvitationToken.token_hash == token_hash).with_for_update()
        )
        invite = res.scalars().first()

        if not invite or invite.used_at or invite.expires_at <= _utcnow_naive():
            raise SmartSellValidationError("Invitation is invalid or expired", "INVITATION_INVALID")

        # Guard against duplicate user creation
        if invite.phone:
            res_u = await db.execute(select(User).where(User.phone.in_(_phone_variants(invite.phone))))
            if res_u.scalars().first():
                raise SmartSellValidationError("User already exists", "USER_EXISTS")
        if invite.email:
            res_e = await db.execute(select(User).where(User.email == invite.email))
            if res_e.scalars().first():
                raise SmartSellValidationError("User already exists", "USER_EXISTS")

        _enforce_password_policy(payload.password, username=invite.phone, email=invite.email)
        user = User(
            company_id=invite.company_id,
            phone=invite.phone,
            email=invite.email,
            full_name=invite.display_name,
            role=invite.role,
            is_active=True,
            is_verified=False,
            hashed_password=get_password_hash(payload.password),
        )
        db.add(user)
        invite.used_at = _utcnow_naive()

    await db.refresh(user)

    access_token, refresh_token = _issue_tokens_for_user(user.id)
    session = UserSession(
        user_id=user.id,
        refresh_token=hashlib.sha256(refresh_token.encode()).hexdigest(),
        ip_address="",
        user_agent="",
        expires_at=_utcnow_naive() + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS),
        is_active=True,
    )
    db.add(session)
    await db.commit()

    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        token_type="bearer",
        expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )


# =============================================================================
# Password reset
# =============================================================================


@router.post(
    "/reset-password",
    response_model=SuccessResponse,
    dependencies=[Depends(auth_rate_limit)],
)
async def reset_password(reset_data: PasswordReset, db: AsyncSession = Depends(get_async_db)):
    """Сброс пароля по OTP (purpose='reset')."""
    phone = _normalize_phone(reset_data.phone)
    verified = await verify_otp_code(db, phone, reset_data.otp_code or "", "reset")
    if not verified:
        raise SmartSellValidationError("Invalid or expired OTP code", "INVALID_OTP")

    user = await _get_user_by_phone(db, phone)
    if not user:
        raise AuthenticationError("User not found", "USER_NOT_FOUND")

    _enforce_password_policy(reset_data.new_password, username=user.phone, email=user.email)
    user.hashed_password = get_password_hash(reset_data.new_password)
    user.failed_login_attempts = 0
    user.locked_until = None

    # инвалидируем все активные refresh-сессии пользователя
    await db.execute(update(UserSession).where(UserSession.user_id == user.id).values(is_active=False))

    await db.commit()

    audit_logger.log_data_change(
        user_id=user.id,
        action="update",
        resource_type="user",
        resource_id=str(user.id),
        changes={"password_reset": True},
    )

    return SuccessResponse(message="Password reset successfully")


# =============================================================================
# Phone change
# =============================================================================


@router.post(
    "/phone/change/request",
    response_model=SuccessResponse,
    dependencies=[Depends(auth_rate_limit), Depends(otp_rate_limit)],
)
async def phone_change_request(
    payload: PhoneChangeRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
    otp_service: OtpProvider = Depends(get_otp_service),
):
    phone = _normalize_phone(payload.new_phone)
    variants = _phone_variants(phone)

    await _enforce_otp_phone_rate_limit(phone)

    if any(v == current_user.phone for v in variants):
        raise SmartSellValidationError("Phone is unchanged", "PHONE_UNCHANGED")

    res = await db.execute(select(User).where(User.phone.in_(variants), User.id != current_user.id))
    if res.scalars().first():
        raise ConflictError("Phone number already in use", "PHONE_IN_USE")

    code, otp_attempt = await create_otp_attempt(
        db,
        phone=phone,
        purpose="phone_change",
        expires_minutes=OTP_TTL_MINUTES,
        attempts_left=OTP_MAX_ATTEMPTS,
        user_id=current_user.id,
    )

    text = _sms_text_for_otp(code, "phone_change")
    send_result: dict[str, Any] | None = None
    provider_name: str | None = None
    provider_version: int | None = None

    try:
        send_result = await otp_service.send_otp(
            phone=phone,
            code=code,
            ttl_seconds=int(timedelta(minutes=OTP_TTL_MINUTES).total_seconds()),
            metadata={"purpose": "phone_change", "text": text},
        )
        provider_name = (send_result or {}).get("provider") or getattr(otp_service, "name", None)
        provider_version = (send_result or {}).get("version") or getattr(otp_service, "version", None)
    except ProviderNotConfiguredError as exc:
        raise HTTPException(status_code=503, detail=exc.code)
    except Exception as exc:
        provider_name = getattr(otp_service, "name", None) or "noop"
        provider_version = getattr(otp_service, "version", None)
        audit_logger.log_system_event(
            level="warning",
            event="otp_send_failed",
            message=str(exc),
            meta={"phone": phone, "purpose": "phone_change", "provider": provider_name},
        )

    audit_logger.log_system_event(
        level="info",
        event="otp_requested",
        message="OTP issued",
        meta={
            "phone": phone,
            "purpose": "phone_change",
            "provider": provider_name or (send_result or {}).get("provider", "none"),
            "provider_version": provider_version,
        },
    )

    data: dict[str, Any] = {
        "expires_in": otp_attempt.seconds_left,
        "provider_success": (send_result or {}).get("success") if send_result else None,
        "provider_status": (send_result or {}).get("status") if send_result else None,
    }

    if _should_return_provider_info():
        data["provider"] = provider_name
        data["provider_version"] = provider_version

    env = str(os.getenv("ENVIRONMENT") or getattr(settings, "ENVIRONMENT", "production") or "production").lower()
    if env != "production" and (provider_name or "noop").startswith("noop"):
        data["dev_code"] = code

    return SuccessResponse(message="OTP code sent successfully", data=data)


@router.post(
    "/phone/change/confirm",
    response_model=SuccessResponse,
    dependencies=[Depends(auth_rate_limit)],
)
async def phone_change_confirm(
    payload: PhoneChangeConfirm,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    phone = _normalize_phone(payload.new_phone)
    variants = _phone_variants(phone)

    verified = await verify_otp_code(db, phone, payload.code or "", "phone_change")
    if not verified:
        raise SmartSellValidationError("Invalid or expired OTP code", "INVALID_OTP")

    res = await db.execute(select(User).where(User.phone.in_(variants), User.id != current_user.id))
    if res.scalars().first():
        raise ConflictError("Phone number already in use", "PHONE_IN_USE")

    user = await db.get(User, current_user.id)
    if not user:
        raise AuthenticationError("User not found", "USER_NOT_FOUND")

    user.phone = phone
    user.is_verified = True
    await db.commit()

    audit_logger.log_data_change(
        user_id=user.id,
        action="update",
        resource_type="user",
        resource_id=str(user.id),
        changes={"phone": phone, "is_verified": True},
    )

    return SuccessResponse(message="Phone updated successfully")


# =============================================================================
# Password reset (token-based)
# =============================================================================


@router.post(
    "/password/reset/request",
    response_model=SuccessResponse,
    dependencies=[Depends(auth_rate_limit)],
)
async def password_reset_request(
    payload: PasswordResetRequest,
    request: Request,
    db: AsyncSession = Depends(get_async_db),
):
    identifier = (payload.identifier or "").strip()
    phone = _normalize_phone(identifier)
    email = (identifier or "").strip().lower()
    client_info = get_client_info(request)

    user = None
    if phone:
        res = await db.execute(select(User).where(User.phone.in_(_phone_variants(phone))))
        user = res.scalars().first()
    if not user and email:
        res_e = await db.execute(select(User).where(User.email == email))
        user = res_e.scalars().first()

    # Always return success to avoid leaking existence
    if not user or not user.email or not user.is_active:
        return SuccessResponse(message="If the account exists, reset instructions were sent")

    token = generate_token()
    token_hash = hash_token(token, secret=getattr(settings, "RESET_TOKEN_SECRET", None))
    reset_obj = PasswordResetToken.build(
        user_id=user.id,
        token_hash=token_hash,
        ttl_minutes=10,
        requested_ip=client_info.get("ip_address"),
        user_agent=client_info.get("user_agent"),
    )
    db.add(reset_obj)
    await db.commit()

    reset_url = f"{_public_url()}/reset-password?token={token}"
    try:
        await send_email(
            to=user.email,
            subject="Password reset",
            body=f"Reset your password: {reset_url}",
            meta={"user_id": user.id},
        )
    except MessagingConfigError:
        if _is_production():
            raise ExternalServiceError(
                "Email provider not configured",
                "EMAIL_PROVIDER_NOT_CONFIGURED",
                http_status=503,
            )
        # In non-prod: log-only via send_email, ignore

    data = {}
    if not _is_production():
        data["reset_url"] = reset_url

    return SuccessResponse(message="If the account exists, reset instructions were sent", data=data)


@router.post(
    "/password/reset/confirm",
    response_model=SuccessResponse,
    dependencies=[Depends(auth_rate_limit)],
)
async def password_reset_confirm(
    payload: PasswordResetConfirm,
    db: AsyncSession = Depends(get_async_db),
):
    token_hash = hash_token(payload.token, secret=getattr(settings, "RESET_TOKEN_SECRET", None))
    async with db.begin():
        res = await db.execute(
            select(PasswordResetToken).where(PasswordResetToken.token_hash == token_hash).with_for_update()
        )
        reset_obj = res.scalars().first()

        if not reset_obj or reset_obj.used_at or reset_obj.expires_at <= _utcnow_naive():
            raise SmartSellValidationError("Invalid or expired token", "RESET_TOKEN_INVALID")

        user = await db.get(User, reset_obj.user_id)
        if not user:
            raise SmartSellValidationError("Invalid or expired token", "RESET_TOKEN_INVALID")

        _enforce_password_policy(payload.new_password, username=user.phone, email=user.email)
        user.hashed_password = get_password_hash(payload.new_password)
        user.failed_login_attempts = 0
        user.locked_until = None
        reset_obj.used_at = _utcnow_naive()
        await db.execute(update(UserSession).where(UserSession.user_id == user.id).values(is_active=False))

    return SuccessResponse(message="Password reset successful")


# =============================================================================
# Совместимостьные алиасы
# =============================================================================


@router.post(
    "/otp/request",
    response_model=SuccessResponse,
    dependencies=[Depends(auth_rate_limit), Depends(otp_rate_limit)],
)
async def request_otp_alias(
    otp_request: OTPRequest,
    db: AsyncSession = Depends(get_async_db),
    otp_service: OtpProvider = Depends(get_otp_service),
):
    return await request_otp(otp_request, db, otp_service=otp_service)  # type: ignore[arg-type]


@router.post(
    "/send-otp",
    response_model=SuccessResponse,
    dependencies=[Depends(auth_rate_limit), Depends(otp_rate_limit)],
)
async def send_otp_alias(
    phone: str = Query(...),
    purpose: str = Query("login"),
    db: AsyncSession = Depends(get_async_db),
    otp_service: OtpProvider = Depends(get_otp_service),
):
    """Алиас для /request-otp (backward compatibility) - принимает query параметры"""
    otp_request = OTPRequest(phone=phone, purpose=purpose)  # type: ignore[arg-type]
    return await request_otp(otp_request, db, otp_service=otp_service)  # type: ignore[arg-type]


@router.post(
    "/otp/verify",
    response_model=SuccessResponse,
    dependencies=[Depends(auth_rate_limit)],
)
async def verify_otp_alias(otp_verify: OTPVerify, db: AsyncSession = Depends(get_async_db)):
    return await verify_otp(otp_verify, db)  # type: ignore[arg-type]


# =============================================================================
# Protected endpoints
# =============================================================================


@router.get("/me")
async def get_me(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
) -> dict:
    """Lightweight current-user profile with basic fields expected by tests."""
    company_id = getattr(current_user, "company_id", None)
    company_name = None
    bin_iin = None
    kaspi_store_id = None
    if company_id:
        row = await db.execute(select(Company).where(Company.id == company_id))
        company = row.scalar_one_or_none()
        if company is not None:
            company_name = getattr(company, "name", None)
            bin_iin = getattr(company, "bin_iin", None)
            kaspi_store_id = getattr(company, "kaspi_store_id", None)
    return {
        "id": current_user.id,
        "phone": current_user.phone,
        "email": current_user.email,
        "first_name": getattr(current_user, "first_name", None),
        "last_name": getattr(current_user, "last_name", None),
        "full_name": getattr(current_user, "full_name", None),
        "company_id": company_id,
        "company_name": company_name,
        "bin_iin": bin_iin,
        "kaspi_store_id": kaspi_store_id,
        "role": getattr(current_user, "role", None),
        "is_active": getattr(current_user, "is_active", True),
        "is_verified": getattr(current_user, "is_verified", False),
        "is_superuser": is_superuser(current_user),
        "last_login_at": getattr(current_user, "last_login_at", None),
        "created_at": getattr(current_user, "created_at", datetime.now(UTC)),
        "updated_at": getattr(current_user, "updated_at", datetime.now(UTC)),
    }
