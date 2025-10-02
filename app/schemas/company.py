"""
Company Pydantic schemas.
"""

from typing import Optional

from pydantic import EmailStr, Field

from app.schemas.base import BaseCreateSchema, BaseResponseSchema, BaseUpdateSchema


class CompanyCreate(BaseCreateSchema):
    """Schema for creating a company"""

    name: str = Field(..., min_length=1, max_length=255)
    bin_iin: Optional[str] = Field(None, max_length=32)
    phone: Optional[str] = Field(None, max_length=32)
    email: Optional[EmailStr] = None
    address: Optional[str] = None
    kaspi_store_id: Optional[str] = Field(None, max_length=64)


class CompanyUpdate(BaseUpdateSchema):
    """Schema for updating a company"""

    name: Optional[str] = Field(None, min_length=1, max_length=255)
    bin_iin: Optional[str] = Field(None, max_length=32)
    phone: Optional[str] = Field(None, max_length=32)
    email: Optional[EmailStr] = None
    address: Optional[str] = None
    kaspi_store_id: Optional[str] = Field(None, max_length=64)
    kaspi_api_key: Optional[str] = Field(None, max_length=255)
    is_active: Optional[bool] = None


class CompanyResponse(BaseResponseSchema):
    """Schema for company response"""

    name: str
    bin_iin: Optional[str]
    phone: Optional[str]
    email: Optional[str]
    address: Optional[str]
    is_active: bool
    kaspi_store_id: Optional[str]
    subscription_plan: str
    subscription_expires_at: Optional[str]


class CompanySettings(BaseCreateSchema):
    """Schema for company settings"""

    timezone: str = "Asia/Almaty"
    currency: str = "KZT"
    language: str = "ru"
    date_format: str = "DD.MM.YYYY"
    time_format: str = "24h"

    # Business settings
    working_hours: Optional[dict] = None
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
