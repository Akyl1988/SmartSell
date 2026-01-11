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
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.db import get_async_db
from app.core.dependencies import (
    auth_rate_limit,
    get_client_info,
    get_current_user,
    get_otp_service,
    otp_rate_limit,
)
from app.core.exceptions import AuthenticationError, ConflictError, SmartSellValidationError
from app.core.logging import audit_logger
from app.core.security import (
    create_access_token,
    create_refresh_token,
    get_password_hash,
    verify_password,
)
from app.integrations.ports.otp import OtpProvider
from app.models.company import Company
from app.models.otp import OtpAttempt, OTPCode  # таблица otp_codes и enterprise-OTP попытки
from app.models.user import User, UserSession
from app.schemas.base import SuccessResponse
from app.schemas.user import (
    OTPRequest,
    OTPVerify,
    PasswordReset,
    RefreshTokenRequest,
    TokenResponse,
    UserCreate,
    UserLogin,
)
from app.utils.otp import verify_otp_hash

router = APIRouter(prefix="/auth", tags=["Authentication"])

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


def _normalize_purpose(v: str | None) -> str:
    return (v or "").strip().lower() or "login"


def _gen_otp_code(length: int = OTP_CODE_LEN) -> str:
    return "".join(str(secrets.randbelow(10)) for _ in range(max(4, min(10, length))))


def _issue_tokens_for_user(user_id: int) -> tuple[str, str]:
    return create_access_token(subject=user_id), create_refresh_token(subject=user_id)


def _sms_text_for_otp(code: str, purpose: str) -> str:
    p = purpose or "login"
    return f"{PROJECT_NAME}: код подтверждения {code} для {p}. Никому не сообщайте."


def _should_return_provider_info() -> bool:
    env = str(os.getenv("ENVIRONMENT") or getattr(settings, "ENVIRONMENT", "production") or "production").lower()
    debug_flag = os.getenv("DEBUG_PROVIDER_INFO", "").strip()
    if debug_flag.lower() in {"1", "true", "yes", "on"}:
        return True
    if env == "production":
        return False
    is_test_env = bool(os.getenv("PYTEST_CURRENT_TEST")) or bool(getattr(settings, "TESTING", False))
    return is_test_env or (env != "production")


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
            refresh_token=hashlib.sha256(refresh_token.encode()).hexdigest(),
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
    phone = _normalize_phone(login_data.phone)
    otp_code = (login_data.otp_code or "").strip()
    via_otp = bool(otp_code)

    user = await _get_user_by_phone(db, phone)

    # Блокировка по неудачным попыткам
    if user and user.locked_until and user.locked_until > _utcnow_naive():
        audit_logger.log_auth_failure(
            username=phone,
            ip_address=client_info["ip_address"],
            reason="Account locked",
        )
        raise AuthenticationError("Account is temporarily locked", "ACCOUNT_LOCKED")

    if via_otp:
        if not user:
            raise AuthenticationError("Invalid OTP code", "INVALID_OTP")

        res = await db.execute(
            select(OtpAttempt)
            .where(
                OtpAttempt.phone.in_(_phone_variants(phone)),
                OtpAttempt.purpose == "login",
                OtpAttempt.expires_at > _utcnow_naive(),
                OtpAttempt.is_verified.is_(False),
            )
            .order_by(OtpAttempt.created_at.desc())
            .limit(1)
        )
        attempt = res.scalars().first()

        if not attempt or not attempt.is_active:
            raise AuthenticationError("Invalid OTP code", "INVALID_OTP")

        if not attempt.verify_with_plain(otp_code, verify_otp_hash, auto_archive=False):
            await db.commit()
            raise AuthenticationError("Invalid OTP code", "INVALID_OTP")

        await db.commit()
    else:
        password = login_data.password or ""
        if not user or not verify_password(password, user.hashed_password):
            if user:
                user.failed_login_attempts = (user.failed_login_attempts or 0) + 1
                if user.failed_login_attempts >= LOGIN_MAX_FAILS:
                    user.locked_until = _utcnow_naive() + timedelta(minutes=LOGIN_LOCK_MINUTES)
                await db.commit()

            audit_logger.log_auth_failure(
                username=phone,
                ip_address=client_info["ip_address"],
                reason="Invalid credentials",
            )
            raise AuthenticationError("Invalid phone number or password", "INVALID_CREDENTIALS")

    if not user.is_active:
        audit_logger.log_auth_failure(
            username=phone,
            ip_address=client_info["ip_address"],
            reason="Inactive account",
        )
        raise AuthenticationError("Account is inactive", "INACTIVE_ACCOUNT")

    # Success
    access_token, refresh_token = _issue_tokens_for_user(user.id)

    session = UserSession(
        user_id=user.id,
        refresh_token=hashlib.sha256(refresh_token.encode()).hexdigest(),
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
async def refresh_token(refresh_data: RefreshTokenRequest, db: AsyncSession = Depends(get_async_db)):
    """
    Обновляет access-токен по действующему refresh-токену.
    Refresh при этом НЕ меняется (скользящая сессия реализуется в другом флоу).
    """
    token_hash = hashlib.sha256(refresh_data.refresh_token.encode()).hexdigest()

    res = await db.execute(
        select(UserSession).where(
            UserSession.refresh_token == token_hash,
            UserSession.is_active.is_(True),
            UserSession.expires_at > _utcnow_naive(),
        )
    )
    session = res.scalars().first()

    if not session:
        raise AuthenticationError("Invalid or expired refresh token", "INVALID_REFRESH_TOKEN")

    res_u = await db.execute(select(User).where(User.id == session.user_id, User.is_active.is_(True)))
    user = res_u.scalars().first()
    if not user:
        raise AuthenticationError("User not found or inactive", "USER_NOT_FOUND")

    access_token, _ = _issue_tokens_for_user(user.id)

    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_data.refresh_token,
        token_type="bearer",
        expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )


@router.post("/token/refresh", response_model=TokenResponse)
async def refresh_token_alias(refresh_data: RefreshTokenRequest, db: AsyncSession = Depends(get_async_db)):
    """Legacy alias for /auth/refresh used by tests/older clients."""
    return await refresh_token(refresh_data, db)


@router.post("/logout", response_model=SuccessResponse)
async def logout(refresh_data: RefreshTokenRequest, db: AsyncSession = Depends(get_async_db)):
    """
    Выход из системы: деактивирует сессию по переданному refresh-токену.
    """
    token_hash = hashlib.sha256(refresh_data.refresh_token.encode()).hexdigest()

    res = await db.execute(
        select(UserSession).where(
            UserSession.refresh_token == token_hash,
            UserSession.is_active.is_(True),
        )
    )
    session = res.scalars().first()

    if session:
        session.is_active = False
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

    now = _utcnow_naive()
    ttl = timedelta(minutes=OTP_TTL_MINUTES)

    res = await db.execute(
        select(OTPCode)
        .where(
            OTPCode.phone == phone,
            OTPCode.purpose == purpose,
            OTPCode.is_used.is_(False),
            OTPCode.expires_at > now,
        )
        .order_by(OTPCode.id.desc())
        .limit(1)
    )
    existing = res.scalars().first()

    if existing:
        # cooldown на повторную отправку
        created_at = getattr(existing, "created_at", None)
        age = (now - created_at).total_seconds() if isinstance(created_at, datetime) else 0.0
        if age < OTP_RESEND_COOLDOWN_SEC:
            return SuccessResponse(
                message="OTP already sent recently",
                data={"expires_in": int((existing.expires_at - now).total_seconds())},
            )

    code = _gen_otp_code()
    if DEBUG_MODE and DEBUG_OTP_CODE:
        code = str(DEBUG_OTP_CODE)

    otp = OTPCode(
        phone=phone,
        code=code,
        purpose=purpose,
        expires_at=now + ttl,
        is_used=False,
        attempts=0,
    )

    try:
        db.add(otp)
        await db.commit()
        await db.refresh(otp)
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

    if DEBUG_MODE:
        print(f"[DEBUG] OTP for {phone}: {code}")

    data = {
        "expires_in": OTP_TTL_MINUTES * 60,
        "provider_success": (send_result or {}).get("success") if send_result else None,
        "provider_status": (send_result or {}).get("status") if send_result else None,
    }

    if _should_return_provider_info():
        data["provider"] = provider_name
        data["provider_version"] = provider_version

    return SuccessResponse(message="OTP code sent successfully", data=data)


@router.post(
    "/verify-otp",
    response_model=SuccessResponse,
    dependencies=[Depends(auth_rate_limit)],
)
async def verify_otp(otp_verify: OTPVerify, db: AsyncSession = Depends(get_async_db)):
    """Проверка OTP кода."""
    phone = _normalize_phone(otp_verify.phone)
    purpose = _normalize_purpose(otp_verify.purpose)

    now = _utcnow_naive()

    dev_master_ok = DEBUG_MODE and DEBUG_OTP_CODE and (str(otp_verify.code).strip() == str(DEBUG_OTP_CODE))

    res = await db.execute(
        select(OTPCode).where(
            OTPCode.phone == phone,
            OTPCode.code == (otp_verify.code or "").strip(),
            OTPCode.purpose == purpose,
            OTPCode.is_used.is_(False),
            OTPCode.expires_at > now,
            OTPCode.attempts < OTP_MAX_ATTEMPTS,
        )
    )
    otp = res.scalars().first()

    if not otp and not dev_master_ok:
        res2 = await db.execute(
            select(OTPCode).where(
                OTPCode.phone == phone,
                OTPCode.purpose == purpose,
                OTPCode.is_used.is_(False),
                OTPCode.expires_at > now,
            )
        )
        existing_otp = res2.scalars().first()
        if existing_otp:
            existing_otp.attempts = (existing_otp.attempts or 0) + 1
            await db.commit()

        raise SmartSellValidationError("Invalid or expired OTP code", "INVALID_OTP")

    if otp:
        otp.is_used = True
    await db.commit()

    if purpose in OTP_PURPOSE_VERIFY_FLAGS:
        user = await _get_user_by_phone(db, phone)
        if user:
            user.is_verified = True
            await db.commit()

    audit_logger.log_system_event(
        level="info",
        event="otp_verified",
        message="OTP verified",
        meta={"phone": phone, "purpose": purpose},
    )

    return SuccessResponse(message="OTP verified successfully")


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

    now = _utcnow_naive()
    res = await db.execute(
        select(OTPCode).where(
            OTPCode.phone == phone,
            OTPCode.code == (reset_data.otp_code or "").strip(),
            OTPCode.purpose == "reset",
            OTPCode.is_used.is_(False),
            OTPCode.expires_at > now,
            OTPCode.attempts < OTP_MAX_ATTEMPTS,
        )
    )
    otp = res.scalars().first()

    if not otp:
        res2 = await db.execute(
            select(OTPCode).where(
                OTPCode.phone == phone,
                OTPCode.purpose == "reset",
                OTPCode.is_used.is_(False),
                OTPCode.expires_at > now,
            )
        )
        existing_otp = res2.scalars().first()
        if existing_otp:
            existing_otp.attempts = (existing_otp.attempts or 0) + 1
            await db.commit()

        raise SmartSellValidationError("Invalid or expired OTP code", "INVALID_OTP")

    user = await _get_user_by_phone(db, phone)
    if not user:
        raise AuthenticationError("User not found", "USER_NOT_FOUND")

    user.hashed_password = get_password_hash(reset_data.new_password)
    user.failed_login_attempts = 0
    user.locked_until = None

    otp.is_used = True

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
async def get_me(current_user: User = Depends(get_current_user)) -> dict:
    """Lightweight current-user profile with basic fields expected by tests."""
    return {
        "id": current_user.id,
        "phone": current_user.phone,
        "email": current_user.email,
        "first_name": getattr(current_user, "first_name", None),
        "last_name": getattr(current_user, "last_name", None),
        "full_name": getattr(current_user, "full_name", None),
        "company_name": getattr(current_user, "company_name", None),
        "bin_iin": getattr(current_user, "bin_iin", None),
        "role": getattr(current_user, "role", None),
        "is_active": getattr(current_user, "is_active", True),
        "is_verified": getattr(current_user, "is_verified", False),
        "is_superuser": getattr(current_user, "is_superuser", False),
        "last_login_at": getattr(current_user, "last_login_at", None),
        "created_at": getattr(current_user, "created_at", datetime.now(UTC)),
        "updated_at": getattr(current_user, "updated_at", datetime.now(UTC)),
    }
