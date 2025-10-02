# app/api/v1/auth.py
"""
Authentication endpoints for user registration, login, and token management.
Production-ready:
- UTC-safe time handling
- Robust error mapping & audit logging
- OTP flow with provider abstraction (Mobizon by default)
- Reuse active OTP (cooldown) and attempt limiting
- Refresh sessions stored as hashed tokens
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple

from fastapi import APIRouter, Depends, Request
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.db import get_db
from app.core.dependencies import auth_rate_limit, get_client_info
from app.core.exceptions import AuthenticationError, ConflictError, SmartSellValidationError
from app.core.logging import audit_logger
from app.core.security import (
    create_access_token,
    create_refresh_token,
    get_password_hash,
    verify_password,
)
from app.models.user import User, UserSession
from app.models.otp import OTPCode  # корректная таблица otp_codes
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

# === SMS provider (централизованный слой) =====================================
_SMS_CLIENT = None

def _get_sms_client_or_none():
    """
    Ленивая инициализация SMS-клиента. По умолчанию ожидается Mobizon
    через app.integrations.sms_base.get_sms_client() и переменную SMS_PROVIDER.
    Если провайдера нет/не сконфигурирован — возвращаем None и не ломаем API.
    """
    global _SMS_CLIENT
    if _SMS_CLIENT is not None:
        return _SMS_CLIENT
    try:
        from app.integrations.sms_base import get_sms_client  # type: ignore
        _SMS_CLIENT = get_sms_client()
        return _SMS_CLIENT
    except Exception:
        # без падений — просто продолжим работать с заглушкой (DEBUG-логирование)
        return None


router = APIRouter(prefix="/auth", tags=["Authentication"])

# === Константы/хелперы ========================================================

def _utcnow() -> datetime:
    # единый безопасный now в UTC
    return datetime.now(timezone.utc)

def _normalize_phone(v: Optional[str]) -> str:
    return (v or "").strip()

def _normalize_email(v: Optional[str]) -> Optional[str]:
    vv = (v or "").strip()
    return vv or None

def _normalize_purpose(v: Optional[str]) -> str:
    return (v or "").strip().lower() or "login"

def _conf(name: str, default):
    # безопасное чтение из настроек
    return getattr(settings, name, default)

# Политики/параметры с дефолтами
OTP_CODE_LEN: int = int(_conf("OTP_CODE_LEN", 6))
OTP_TTL_MINUTES: int = int(_conf("OTP_TTL_MINUTES", 10))
OTP_MAX_ATTEMPTS: int = int(_conf("OTP_MAX_ATTEMPTS", 3))
OTP_RESEND_COOLDOWN_SEC: int = int(_conf("OTP_RESEND_COOLDOWN_SEC", 60))
LOGIN_MAX_FAILS: int = int(_conf("LOGIN_MAX_FAILS", 5))
LOGIN_LOCK_MINUTES: int = int(_conf("LOGIN_LOCK_MINUTES", 15))
PROJECT_NAME: str = str(_conf("PROJECT_NAME", "SmartSell"))
DEBUG_MODE: bool = bool(_conf("DEBUG", False))
DEBUG_OTP_CODE: Optional[str] = getattr(settings, "DEBUG_OTP_CODE", None)

# Цели OTP, по которым подтверждаем учётку
OTP_PURPOSE_VERIFY_FLAGS = {"registration", "register", "verify"}

# === Вспомогалки ===============================================================

def _gen_otp_code(length: int = OTP_CODE_LEN) -> str:
    # криптоустойчивые случайные цифры
    return "".join(str(secrets.randbelow(10)) for _ in range(max(4, min(10, length))))

def _issue_tokens_for_user(user_id: int) -> Tuple[str, str]:
    return create_access_token(subject=user_id), create_refresh_token(subject=user_id)

def _sms_text_for_otp(code: str, purpose: str) -> str:
    # короткий и понятный текст; альяс-имя задаётся у провайдера
    p = purpose or "login"
    return f"{PROJECT_NAME}: код подтверждения {code} для {p}. Никому не сообщайте."

def _send_otp_via_provider(phone: str, text: str) -> dict | None:
    client = _get_sms_client_or_none()
    if client is None:
        return None
    try:
        return client.send_sms(recipient=phone, text=text)
    except Exception as e:
        # не валим API из-за внешнего провайдера — просто лог
        audit_logger.log_system_event(
            level="warning",
            event="sms_send_failed",
            message=str(e),
            meta={"phone": phone},
        )
        return {"success": False, "error": str(e)}

# === Health ===================================================================

@router.get("/health", response_model=SuccessResponse)
async def health():
    provider = None
    try:
        from app.integrations.sms_base import get_sms_client  # type: ignore
        provider = type(get_sms_client()).__name__
    except Exception:
        provider = "unconfigured"
    return SuccessResponse(message="auth ok", data={
        "provider": provider,
        "otp_ttl_minutes": OTP_TTL_MINUTES,
        "otp_max_attempts": OTP_MAX_ATTEMPTS,
    })

# === Endpoints ================================================================

@router.post(
    "/register",
    response_model=SuccessResponse,
    dependencies=[Depends(auth_rate_limit)],
)
async def register(user_data: UserCreate, request: Request, db: Session = Depends(get_db)):
    """Register a new user."""
    client_info = get_client_info(request)

    phone = _normalize_phone(user_data.phone)
    email = _normalize_email(user_data.email)

    # Check if user already exists by phone
    existing_user = db.query(User).filter(User.phone == phone).first()
    if existing_user:
        audit_logger.log_auth_failure(
            username=phone,
            ip_address=client_info["ip_address"],
            reason="User already exists",
        )
        raise ConflictError("User with this phone number already exists", "USER_EXISTS")

    # Check email uniqueness if provided
    if email:
        existing_email = db.query(User).filter(User.email == email).first()
        if existing_email:
            raise ConflictError("User with this email already exists", "EMAIL_EXISTS")

    try:
        # Create new user
        hashed_password = get_password_hash(user_data.password)
        user = User(
            phone=phone,
            email=email,
            full_name=user_data.full_name,
            hashed_password=hashed_password,
            is_active=True,
            is_verified=False,
        )

        db.add(user)
        db.commit()
        db.refresh(user)

        audit_logger.log_data_change(
            user_id=user.id,
            action="create",
            resource_type="user",
            resource_id=str(user.id),
            changes={"phone": phone, "email": email},
        )

        # По желанию можно сразу отправить OTP для регистрации
        # (не критично: фронт обычно вызывает /request-otp сам)
        return SuccessResponse(
            message="User registered successfully. Please verify your phone number.",
            data={"user_id": user.id},
        )

    except IntegrityError as e:
        db.rollback()
        msg = str(getattr(e, "orig", e))
        if "phone" in msg:
            raise ConflictError("Phone number already registered", "DUPLICATE_PHONE")
        if "email" in msg:
            raise ConflictError("Email already registered", "DUPLICATE_EMAIL")
        raise ConflictError("Registration failed due to data conflict", "REGISTRATION_FAILED")


@router.post(
    "/login",
    response_model=TokenResponse,
    dependencies=[Depends(auth_rate_limit)],
)
async def login(login_data: UserLogin, request: Request, db: Session = Depends(get_db)):
    """Authenticate user and return tokens."""
    client_info = get_client_info(request)
    phone = _normalize_phone(login_data.phone)

    user = db.query(User).filter(User.phone == phone).first()

    # Проверка блокировки
    if user and user.locked_until and user.locked_until > _utcnow():
        audit_logger.log_auth_failure(
            username=phone,
            ip_address=client_info["ip_address"],
            reason="Account locked",
        )
        raise AuthenticationError("Account is temporarily locked", "ACCOUNT_LOCKED")

    # Проверка пароля
    if not user or not verify_password(login_data.password, user.hashed_password):
        # инкремент fail-счетчика и возможная блокировка
        if user:
            user.failed_login_attempts = (user.failed_login_attempts or 0) + 1
            if user.failed_login_attempts >= LOGIN_MAX_FAILS:
                user.locked_until = _utcnow() + timedelta(minutes=LOGIN_LOCK_MINUTES)
            db.commit()

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

    # Success: выдаем токены и сбрасываем флаги
    access_token, refresh_token = _issue_tokens_for_user(user.id)

    session = UserSession(
        user_id=user.id,
        refresh_token=hashlib.sha256(refresh_token.encode()).hexdigest(),
        ip_address=client_info["ip_address"],
        user_agent=client_info["user_agent"],
        expires_at=_utcnow() + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS),
        is_active=True,
    )
    db.add(session)

    user.last_login_at = _utcnow()
    user.failed_login_attempts = 0
    user.locked_until = None

    db.commit()

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
async def refresh_token(refresh_data: RefreshTokenRequest, db: Session = Depends(get_db)):
    """Refresh access token using refresh token."""
    token_hash = hashlib.sha256(refresh_data.refresh_token.encode()).hexdigest()

    # Find active session
    session = (
        db.query(UserSession)
        .filter(
            UserSession.refresh_token == token_hash,
            UserSession.is_active.is_(True),
            UserSession.expires_at > _utcnow(),
        )
        .first()
    )

    if not session:
        raise AuthenticationError("Invalid or expired refresh token", "INVALID_REFRESH_TOKEN")

    # Get user
    user = db.query(User).filter(User.id == session.user_id, User.is_active.is_(True)).first()
    if not user:
        raise AuthenticationError("User not found or inactive", "USER_NOT_FOUND")

    access_token, _ = _issue_tokens_for_user(user.id)

    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_data.refresh_token,  # Keep the same refresh token
        token_type="bearer",
        expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )


@router.post("/logout", response_model=SuccessResponse)
async def logout(refresh_data: RefreshTokenRequest, db: Session = Depends(get_db)):
    """Logout user by invalidating refresh token."""
    token_hash = hashlib.sha256(refresh_data.refresh_token.encode()).hexdigest()

    session = (
        db.query(UserSession)
        .filter(UserSession.refresh_token == token_hash, UserSession.is_active.is_(True))
        .first()
    )

    if session:
        session.is_active = False
        db.commit()

    return SuccessResponse(message="Logged out successfully")


# === OTP: request =============================================================

@router.post(
    "/request-otp",
    response_model=SuccessResponse,
    dependencies=[Depends(auth_rate_limit)],
)
async def request_otp(otp_request: OTPRequest, db: Session = Depends(get_db)):
    """
    Request OTP code for phone verification.
    Поведение:
      - если уже есть активная OTP (не истекла) — переиспользуем (cooldown),
        но не раскрываем сам код в ответе;
      - при DEBUG=True пишем код в stdout (только на dev).
    """
    phone = _normalize_phone(otp_request.phone)
    purpose = _normalize_purpose(otp_request.purpose)

    now = _utcnow()
    ttl = timedelta(minutes=OTP_TTL_MINUTES)

    # есть ли свежая активная OTP (анти-спам/троттлинг)
    existing = (
        db.query(OTPCode)
        .filter(
            OTPCode.phone == phone,
            OTPCode.purpose == purpose,
            OTPCode.is_used.is_(False),
            OTPCode.expires_at > now,
        )
        .order_by(OTPCode.id.desc())
        .first()
    )

    if existing:
        # Проверим cooldown для повторной отправки
        created_at = getattr(existing, "created_at", None)
        if created_at and isinstance(created_at, datetime):
            age = (now - created_at).total_seconds()
        else:
            # fallback — считаем по TTL, если нет явного created_at
            age = 0
        if age < OTP_RESEND_COOLDOWN_SEC:
            # Не шлём вторую СМС слишком рано — просто говорим, что уже отправлено
            return SuccessResponse(
                message="OTP already sent recently",
                data={"expires_in": int((existing.expires_at - now).total_seconds())},
            )

    # генерим код
    code = _gen_otp_code()
    # в DEBUG можно переопределить код
    if DEBUG_MODE and DEBUG_OTP_CODE:
        code = str(DEBUG_OTP_CODE)

    otp = OTPCode(
        phone=phone,
        code=code,
        purpose=purpose,
        expires_at=now + ttl,
        # попытки/used расставит модель по умолчанию; оставим явные значения для читабельности
        is_used=False,
        attempts=0,
    )

    try:
        db.add(otp)
        db.commit()
        db.refresh(otp)
    except IntegrityError:
        db.rollback()
        raise SmartSellValidationError("Failed to create OTP", "OTP_CREATE_FAILED")

    # Отправка через провайдера (если настроен)
    text = _sms_text_for_otp(code, purpose)
    send_result = _send_otp_via_provider(phone, text)

    audit_logger.log_system_event(
        level="info",
        event="otp_requested",
        message="OTP issued",
        meta={"phone": phone, "purpose": purpose, "provider": (send_result or {}).get("provider", "none")},
    )

    if DEBUG_MODE:
        # В проде — не логгируй реальные коды
        print(f"[DEBUG] OTP for {phone}: {code}")

    # Не раскрываем код в ответе
    return SuccessResponse(
        message="OTP code sent successfully",
        data={
            "expires_in": OTP_TTL_MINUTES * 60,
            "provider_success": (send_result or {}).get("success") if send_result else None,
        },
    )


# === OTP: verify ==============================================================

@router.post(
    "/verify-otp",
    response_model=SuccessResponse,
    dependencies=[Depends(auth_rate_limit)],
)
async def verify_otp(otp_verify: OTPVerify, db: Session = Depends(get_db)):
    """Verify OTP code."""
    phone = _normalize_phone(otp_verify.phone)
    purpose = _normalize_purpose(otp_verify.purpose)

    now = _utcnow()

    # dev-бэкдор: если DEBUG_OTP_CODE задан, позволим принять его как корректный
    dev_master_ok = DEBUG_MODE and DEBUG_OTP_CODE and (str(otp_verify.code).strip() == str(DEBUG_OTP_CODE))

    otp = (
        db.query(OTPCode)
        .filter(
            OTPCode.phone == phone,
            OTPCode.code == (otp_verify.code or "").strip(),
            OTPCode.purpose == purpose,
            OTPCode.is_used.is_(False),
            OTPCode.expires_at > now,
            OTPCode.attempts < OTP_MAX_ATTEMPTS,
        )
        .first()
    )

    if not otp and not dev_master_ok:
        # инкремент попыток по активной записи (если есть)
        existing_otp = (
            db.query(OTPCode)
            .filter(
                OTPCode.phone == phone,
                OTPCode.purpose == purpose,
                OTPCode.is_used.is_(False),
                OTPCode.expires_at > now,
            )
            .first()
        )
        if existing_otp:
            existing_otp.attempts = (existing_otp.attempts or 0) + 1
            db.commit()

        raise SmartSellValidationError("Invalid or expired OTP code", "INVALID_OTP")

    # помечаем использованной
    if otp:
        otp.is_used = True
    db.commit()

    # при целях верификации — помечаем пользователя как верифицированного
    if purpose in OTP_PURPOSE_VERIFY_FLAGS:
        user = db.query(User).filter(User.phone == phone).first()
        if user:
            user.is_verified = True
            db.commit()

    audit_logger.log_system_event(
        level="info",
        event="otp_verified",
        message="OTP verified",
        meta={"phone": phone, "purpose": purpose},
    )

    return SuccessResponse(message="OTP verified successfully")


# === Password reset ===========================================================

@router.post(
    "/reset-password",
    response_model=SuccessResponse,
    dependencies=[Depends(auth_rate_limit)],
)
async def reset_password(reset_data: PasswordReset, db: Session = Depends(get_db)):
    """Reset user password using OTP."""
    phone = _normalize_phone(reset_data.phone)

    now = _utcnow()
    otp = (
        db.query(OTPCode)
        .filter(
            OTPCode.phone == phone,
            OTPCode.code == (reset_data.otp_code or "").strip(),
            OTPCode.purpose == "reset",
            OTPCode.is_used.is_(False),
            OTPCode.expires_at > now,
            OTPCode.attempts < OTP_MAX_ATTEMPTS,
        )
        .first()
    )

    if not otp:
        existing_otp = (
            db.query(OTPCode)
            .filter(
                OTPCode.phone == phone,
                OTPCode.purpose == "reset",
                OTPCode.is_used.is_(False),
                OTPCode.expires_at > now,
            )
            .first()
        )
        if existing_otp:
            existing_otp.attempts = (existing_otp.attempts or 0) + 1
            db.commit()

        raise SmartSellValidationError("Invalid or expired OTP code", "INVALID_OTP")

    user = db.query(User).filter(User.phone == phone).first()
    if not user:
        raise AuthenticationError("User not found", "USER_NOT_FOUND")

    user.hashed_password = get_password_hash(reset_data.new_password)
    user.failed_login_attempts = 0
    user.locked_until = None

    otp.is_used = True

    # инвалидируем все активные сессии
    db.query(UserSession).filter(UserSession.user_id == user.id).update({"is_active": False})

    db.commit()

    audit_logger.log_data_change(
        user_id=user.id,
        action="update",
        resource_type="user",
        resource_id=str(user.id),
        changes={"password_reset": True},
    )

    return SuccessResponse(message="Password reset successfully")


# === Совместимостьные алиасы (по желанию фронта) ==============================
# /otp/request и /otp/verify маппим на те же обработчики

@router.post("/otp/request", response_model=SuccessResponse, dependencies=[Depends(auth_rate_limit)])
async def request_otp_alias(otp_request: OTPRequest, db: Session = Depends(get_db)):
    return await request_otp(otp_request, db)  # type: ignore[arg-type]

@router.post("/otp/verify", response_model=SuccessResponse, dependencies=[Depends(auth_rate_limit)])
async def verify_otp_alias(otp_verify: OTPVerify, db: Session = Depends(get_db)):
    return await verify_otp(otp_verify, db)  # type: ignore[arg-type]
