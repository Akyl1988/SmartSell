"""
User-related Pydantic schemas with validation.
"""

import re
from datetime import datetime
from typing import Optional

from pydantic import EmailStr, Field, validator

from app.schemas.base import BaseSchema, TimestampedSchema


class UserBase(BaseSchema):
    """Base user schema."""

    phone: str = Field(..., min_length=10, max_length=20, description="Phone number")
    email: Optional[EmailStr] = Field(None, description="Email address")
    full_name: Optional[str] = Field(None, min_length=2, max_length=255, description="Full name")

    @validator("phone")
    def validate_phone(cls, v):
        """Validate phone number format."""
        # Remove all non-digit characters
        phone_digits = re.sub(r"\D", "", v)

        # Check if it's a valid length (10-15 digits)
        if len(phone_digits) < 10 or len(phone_digits) > 15:
            raise ValueError("Phone number must contain 10-15 digits")

        # Ensure it starts with country code or local format
        if not phone_digits.startswith(("7", "8", "77", "87")):
            raise ValueError("Invalid phone number format")

        return phone_digits

    @validator("full_name")
    def validate_full_name(cls, v):
        """Validate full name."""
        if v and not re.match(r"^[a-zA-Zа-яА-Я\s\-\'\.]+$", v):
            raise ValueError("Full name contains invalid characters")
        return v


class UserCreate(UserBase):
    """Schema for user creation."""

    password: str = Field(..., min_length=8, max_length=100, description="Password")
    confirm_password: str = Field(..., description="Password confirmation")

    @validator("password")
    def validate_password(cls, v):
        """Validate password strength."""
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters long")

        if not re.search(r"[A-Za-z]", v):
            raise ValueError("Password must contain at least one letter")

        if not re.search(r"\d", v):
            raise ValueError("Password must contain at least one digit")

        return v

    @validator("confirm_password")
    def passwords_match(cls, v, values):
        """Validate password confirmation."""
        if "password" in values and v != values["password"]:
            raise ValueError("Passwords do not match")
        return v


class UserLogin(BaseSchema):
    """Schema for user login."""

    phone: str = Field(..., description="Phone number")
    password: str = Field(..., description="Password")


class UserResponse(UserBase, TimestampedSchema):
    """Schema for user response."""

    is_active: bool
    is_verified: bool
    is_superuser: bool
    last_login_at: Optional[datetime]


class UserUpdate(BaseSchema):
    """Schema for user update."""

    email: Optional[EmailStr] = None
    full_name: Optional[str] = Field(None, min_length=2, max_length=255)

    @validator("full_name")
    def validate_full_name(cls, v):
        """Validate full name."""
        if v and not re.match(r"^[a-zA-Zа-яА-Я\s\-\'\.]+$", v):
            raise ValueError("Full name contains invalid characters")
        return v


class TokenResponse(BaseSchema):
    """Schema for token response."""

    access_token: str = Field(..., description="JWT access token")
    refresh_token: str = Field(..., description="JWT refresh token")
    token_type: str = Field(default="bearer", description="Token type")
    expires_in: int = Field(..., description="Token expiration time in seconds")


class RefreshTokenRequest(BaseSchema):
    """Schema for refresh token request."""

    refresh_token: str = Field(..., description="Refresh token")


class OTPRequest(BaseSchema):
    """Schema for OTP request."""

    phone: str = Field(..., description="Phone number")
    purpose: str = Field(..., pattern=r"^(registration|login|reset)$", description="OTP purpose")


class OTPVerify(BaseSchema):
    """Schema for OTP verification."""

    phone: str = Field(..., description="Phone number")
    code: str = Field(..., min_length=4, max_length=6, description="OTP code")
    purpose: str = Field(..., pattern=r"^(registration|login|reset)$", description="OTP purpose")


class PasswordReset(BaseSchema):
    """Schema for password reset."""

    phone: str = Field(..., description="Phone number")
    otp_code: str = Field(..., min_length=4, max_length=6, description="OTP code")
    new_password: str = Field(..., min_length=8, max_length=100, description="New password")
    confirm_password: str = Field(..., description="Password confirmation")

    @validator("new_password")
    def validate_password(cls, v):
        """Validate password strength."""
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters long")

        if not re.search(r"[A-Za-z]", v):
            raise ValueError("Password must contain at least one letter")

        if not re.search(r"\d", v):
            raise ValueError("Password must contain at least one digit")

        return v

    @validator("confirm_password")
    def passwords_match(cls, v, values):
        """Validate password confirmation."""
        if "new_password" in values and v != values["new_password"]:
            raise ValueError("Passwords do not match")
        return v
