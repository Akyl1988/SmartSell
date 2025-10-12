"""
User-related Pydantic schemas with validation.

Цели файла:
- Единые, самодокументированные схемы для пользовательских операций (регистрация, логин, OTP, токены, обновление профиля и т.д.).
- Современные валидаторы Pydantic v2: field_validator / model_validator.
- Нормализация телефона (очистка от нецифровых символов, мягкие правила длины).
- Избавление от 422 в /api/auth/register, когда тест не передаёт confirm_password:
  confirm_password опционален и подставляется из password на этапе model_validator(mode="before").
- Производственный уровень: чёткие ошибки, стабильные типы, комментарии для дальнейшего расширения.

Замечания:
- Базовые классы BaseSchema, TimestampedSchema предполагаются совместимыми с Pydantic v2 (BaseModel-наследники).
- В UserCreate включены поля first_name/last_name/company_name/bin_iin как optional,
  чтобы быть совместимыми с существующими тестами и интеграциями.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import EmailStr, Field, field_validator, model_validator

from app.schemas.base import BaseSchema, TimestampedSchema

__all__ = [
    "UserBase",
    "UserCreate",
    "UserLogin",
    "UserResponse",
    "UserUpdate",
    "TokenResponse",
    "RefreshTokenRequest",
    "OTPRequest",
    "OTPVerify",
    "PasswordReset",
]

# -----------------------------------------------------------------------------
# Вспомогательные функции/константы для валидации
# -----------------------------------------------------------------------------

_NAME_REGEX = re.compile(r"^[a-zA-Zа-яА-Я\s\-'\.]+$")
_PURPOSE_ALLOWED = {"registration", "login", "reset"}


def _normalize_phone(raw: str) -> str:
    """Оставляем только цифры. Не навязываем строгий формат — проверим длину и начало."""
    return re.sub(r"\D", "", raw or "")


def _validate_phone_digits(phone_digits: str) -> None:
    """
    Универсальная проверка "разумности" номера:
    - 10..15 цифр
    - начинается на 7/8/77/87 (мягкое правило под локальные кейсы проекта)
    """
    if len(phone_digits) < 10 or len(phone_digits) > 15:
        raise ValueError("Phone number must contain 10-15 digits")
    if not phone_digits.startswith(("7", "8", "77", "87")):
        # мягкая проверка — на проде можно заменить на строгую валидацию по E.164
        raise ValueError("Invalid phone number format")


def _password_strength_check(value: str) -> None:
    """Базовая проверка сложности пароля (настройки поднимаем по мере надобности)."""
    if len(value) < 8:
        raise ValueError("Password must be at least 8 characters long")
    if not re.search(r"[A-Za-z]", value):
        raise ValueError("Password must contain at least one letter")
    if not re.search(r"\d", value):
        raise ValueError("Password must contain at least one digit")


# -----------------------------------------------------------------------------
# Базовые и общие схемы
# -----------------------------------------------------------------------------


class UserBase(BaseSchema):
    """Base user schema."""

    phone: str = Field(..., min_length=10, max_length=20, description="Phone number")
    email: Optional[EmailStr] = Field(None, description="Email address")
    full_name: Optional[str] = Field(None, min_length=2, max_length=255, description="Full name")

    # Доп. атрибуты организации (часто встречаются в наших ручках)
    company_name: Optional[str] = Field(None, max_length=255, description="Company name")
    bin_iin: Optional[str] = Field(
        None,
        min_length=6,
        max_length=20,
        description="Company BIN/IIN (if applicable)",
        examples=["123456789012"],
    )

    @field_validator("phone", mode="before")
    @classmethod
    def _normalize_and_validate_phone(cls, v: str) -> str:
        """Очистка и базовая проверка телефона."""
        digits = _normalize_phone(v)
        _validate_phone_digits(digits)
        return digits

    @field_validator("full_name")
    @classmethod
    def _validate_full_name(cls, v: Optional[str]) -> Optional[str]:
        """Допускаем латиницу/кириллицу, пробелы, дефис, апостроф и точку."""
        if v and not _NAME_REGEX.match(v):
            raise ValueError("Full name contains invalid characters")
        return v


class UserCreate(UserBase):
    """
    Schema for user creation / registration.

    Особенности:
    - confirm_password опционален: если не передали — он автоматически приравнивается к password.
    - Поддерживает first_name/last_name для обратной совместимости с клиентами/тестами.
    """

    password: str = Field(..., min_length=8, max_length=100, description="Password")
    confirm_password: Optional[str] = Field(
        None, description="Password confirmation (optional; defaults to password)"
    )

    # Поля для совместимости со старыми клиентами/тестами
    first_name: Optional[str] = Field(None, min_length=1, max_length=100)
    last_name: Optional[str] = Field(None, min_length=1, max_length=100)

    @model_validator(mode="before")
    @classmethod
    def _prefill_confirm_password(cls, data: Any) -> Any:
        """
        На этапе "before" заполняем confirm_password из password, если не пришёл.
        Также, если присутствуют first_name/last_name и нет full_name — соберём full_name.
        """
        if isinstance(data, dict):
            if data.get("confirm_password") in (None, ""):
                data["confirm_password"] = data.get("password")
            # автоматически формируем full_name при наличии составных полей
            fn = (data.get("first_name") or "").strip()
            ln = (data.get("last_name") or "").strip()
            if not data.get("full_name") and (fn or ln):
                full_name = f"{fn} {ln}".strip()
                if full_name:
                    data["full_name"] = full_name
        return data

    @field_validator("first_name", "last_name")
    @classmethod
    def _validate_names(cls, v: Optional[str]) -> Optional[str]:
        if v and not _NAME_REGEX.match(v):
            raise ValueError("Name contains invalid characters")
        return v

    @field_validator("password")
    @classmethod
    def _validate_password_strength(cls, v: str) -> str:
        _password_strength_check(v)
        return v

    @model_validator(mode="after")
    def _passwords_match(self) -> UserCreate:
        if self.confirm_password != self.password:
            raise ValueError("Passwords do not match")
        return self


class UserLogin(BaseSchema):
    """Schema for user login."""

    phone: str = Field(..., description="Phone number")
    password: str = Field(..., description="Password")

    @field_validator("phone", mode="before")
    @classmethod
    def _normalize_and_validate_phone(cls, v: str) -> str:
        digits = _normalize_phone(v)
        _validate_phone_digits(digits)
        return digits


class UserResponse(UserBase, TimestampedSchema):
    """Schema for user response (отдаём наружу)."""

    id: int | str = Field(..., description="User ID")
    is_active: bool = Field(default=True)
    is_verified: bool = Field(default=False)
    is_superuser: bool = Field(default=False)
    last_login_at: Optional[datetime] = None


class UserUpdate(BaseSchema):
    """Schema for user update."""

    email: Optional[EmailStr] = None
    full_name: Optional[str] = Field(None, min_length=2, max_length=255)
    company_name: Optional[str] = Field(None, max_length=255)
    bin_iin: Optional[str] = Field(None, min_length=6, max_length=20)

    @field_validator("full_name")
    @classmethod
    def _validate_full_name(cls, v: Optional[str]) -> Optional[str]:
        if v and not _NAME_REGEX.match(v):
            raise ValueError("Full name contains invalid characters")
        return v


# -----------------------------------------------------------------------------
# Токены / Refresh
# -----------------------------------------------------------------------------


class TokenResponse(BaseSchema):
    """Schema for token response."""

    access_token: str = Field(..., description="JWT access token")
    refresh_token: str = Field(..., description="JWT refresh token")
    token_type: Literal["bearer"] = Field(default="bearer", description="Token type")
    expires_in: int = Field(..., description="Token expiration time in seconds")


class RefreshTokenRequest(BaseSchema):
    """Schema for refresh token request."""

    refresh_token: str = Field(..., description="Refresh token")


# -----------------------------------------------------------------------------
# OTP
# -----------------------------------------------------------------------------


class OTPRequest(BaseSchema):
    """Schema for OTP request."""

    phone: str = Field(..., description="Phone number")
    purpose: Literal["registration", "login", "reset"] = Field(..., description="OTP purpose")

    @field_validator("phone", mode="before")
    @classmethod
    def _normalize_and_validate_phone(cls, v: str) -> str:
        digits = _normalize_phone(v)
        _validate_phone_digits(digits)
        return digits


class OTPVerify(BaseSchema):
    """Schema for OTP verification."""

    phone: str = Field(..., description="Phone number")
    code: str = Field(..., min_length=4, max_length=6, description="OTP code")
    purpose: Literal["registration", "login", "reset"] = Field(..., description="OTP purpose")

    @field_validator("phone", mode="before")
    @classmethod
    def _normalize_and_validate_phone(cls, v: str) -> str:
        digits = _normalize_phone(v)
        _validate_phone_digits(digits)
        return digits

    @field_validator("code")
    @classmethod
    def _validate_code_digits(cls, v: str) -> str:
        if not v or not v.isdigit():
            raise ValueError("OTP code must contain digits only")
        if not (4 <= len(v) <= 6):
            raise ValueError("OTP code length must be between 4 and 6")
        return v


# -----------------------------------------------------------------------------
# Сброс пароля
# -----------------------------------------------------------------------------


class PasswordReset(BaseSchema):
    """Schema for password reset."""

    phone: str = Field(..., description="Phone number")
    otp_code: str = Field(..., min_length=4, max_length=6, description="OTP code")
    new_password: str = Field(..., min_length=8, max_length=100, description="New password")
    # confirm_password опционален с автоподстановкой — та же стратегия, что и при регистрации
    confirm_password: Optional[str] = Field(
        None, description="Password confirmation (optional; defaults to new_password)"
    )

    @model_validator(mode="before")
    @classmethod
    def _prefill_confirm_password(cls, data: Any) -> Any:
        """До построения модели заполняем confirm_password, если он не пришёл."""
        if isinstance(data, dict):
            if data.get("confirm_password") in (None, ""):
                data["confirm_password"] = data.get("new_password")
        return data

    @field_validator("phone", mode="before")
    @classmethod
    def _normalize_and_validate_phone(cls, v: str) -> str:
        digits = _normalize_phone(v)
        _validate_phone_digits(digits)
        return digits

    @field_validator("otp_code")
    @classmethod
    def _validate_code_digits(cls, v: str) -> str:
        if not v or not v.isdigit():
            raise ValueError("OTP code must contain digits only")
        if not (4 <= len(v) <= 6):
            raise ValueError("OTP code length must be between 4 and 6")
        return v

    @field_validator("new_password")
    @classmethod
    def _validate_password_strength(cls, v: str) -> str:
        _password_strength_check(v)
        return v

    @model_validator(mode="after")
    def _passwords_match(self) -> PasswordReset:
        if self.confirm_password != self.new_password:
            raise ValueError("Passwords do not match")
        return self
