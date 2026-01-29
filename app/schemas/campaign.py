from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.campaign import CampaignStatus, MessageStatus


class MessageCreate(BaseModel):
    recipient: str = Field(..., min_length=1, max_length=255)
    content: str = Field(..., min_length=1, max_length=2000)
    status: MessageStatus = MessageStatus.PENDING  # Explicit status specification

    @field_validator("recipient")
    def _validate_recipient(cls, v: str) -> str:
        vv = (v or "").strip()
        if not vv:
            raise ValueError("recipient must be non-empty")
        return vv

    @field_validator("content")
    def _validate_content(cls, v: str) -> str:
        vv = (v or "").strip()
        if not vv:
            raise ValueError("content must be non-empty")
        return vv


class MessageResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    campaign_id: int
    recipient: str
    content: str
    status: MessageStatus
    created_at: datetime
    sent_at: datetime | None = None
    error_message: str | None = None


class CampaignCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=255)
    description: str | None = Field(None, max_length=2000)
    scheduled_at: datetime | None = None
    messages: list[MessageCreate] = Field(default_factory=list)

    @field_validator("title")
    def _validate_title(cls, v: str) -> str:
        vv = (v or "").strip()
        if not vv:
            raise ValueError("title must be non-empty")
        return vv

    @field_validator("description", mode="before")
    def _normalize_description(cls, v: str | None) -> str | None:
        if v is None:
            return None
        vv = (v or "").strip()
        return vv or None


class CampaignUpdate(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=255)
    description: str | None = Field(None, max_length=2000)
    status: CampaignStatus | None = None
    scheduled_at: datetime | None = None

    @field_validator("title")
    def _validate_title(cls, v: str | None) -> str | None:
        if v is None:
            return None
        vv = (v or "").strip()
        if not vv:
            raise ValueError("title must be non-empty")
        return vv

    @field_validator("description", mode="before")
    def _normalize_description(cls, v: str | None) -> str | None:
        if v is None:
            return None
        vv = (v or "").strip()
        return vv or None


class CampaignResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    description: str | None = None
    status: CampaignStatus
    created_at: datetime
    updated_at: datetime
    scheduled_at: datetime | None = None
    messages: list[MessageResponse] = Field(default_factory=list)
