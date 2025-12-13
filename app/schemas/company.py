"""
Company Pydantic schemas.
"""


from pydantic import EmailStr, Field

from app.schemas.base import BaseCreateSchema, BaseResponseSchema, BaseUpdateSchema


class CompanyCreate(BaseCreateSchema):
    """Schema for creating a company"""

    name: str = Field(..., min_length=1, max_length=255)
    bin_iin: str | None = Field(None, max_length=32)
    phone: str | None = Field(None, max_length=32)
    email: EmailStr | None = None
    address: str | None = None
    kaspi_store_id: str | None = Field(None, max_length=64)


class CompanyUpdate(BaseUpdateSchema):
    """Schema for updating a company"""

    name: str | None = Field(None, min_length=1, max_length=255)
    bin_iin: str | None = Field(None, max_length=32)
    phone: str | None = Field(None, max_length=32)
    email: EmailStr | None = None
    address: str | None = None
    kaspi_store_id: str | None = Field(None, max_length=64)
    kaspi_api_key: str | None = Field(None, max_length=255)
    is_active: bool | None = None


class CompanyResponse(BaseResponseSchema):
    """Schema for company response"""

    name: str
    bin_iin: str | None
    phone: str | None
    email: str | None
    address: str | None
    is_active: bool
    kaspi_store_id: str | None
    subscription_plan: str
    subscription_expires_at: str | None


class CompanySettings(BaseCreateSchema):
    """Schema for company settings"""

    timezone: str = "Asia/Almaty"
    currency: str = "KZT"
    language: str = "ru"
    date_format: str = "DD.MM.YYYY"
    time_format: str = "24h"

    # Business settings
    working_hours: dict | None = None
    night_mode_enabled: bool = True
    night_mode_start: str = "22:00"
    night_mode_end: str = "08:00"

    # Notification settings
    email_notifications: bool = True
    sms_notifications: bool = True
    whatsapp_notifications: bool = False

    # Integration settings
    auto_sync_kaspi: bool = True
    sync_interval_minutes: int = 15
