from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field, field_validator

from app.api.v1.campaigns_helpers import _parse_dt, _utcnow


class MessageStatus(str, Enum):
    pending = "pending"
    sent = "sent"
    delivered = "delivered"
    failed = "failed"
    read = "read"
    scheduled = "scheduled"
    cancelled = "cancelled"


class ChannelType(str, Enum):
    email = "email"
    whatsapp = "whatsapp"
    telegram = "telegram"
    sms = "sms"
    push = "push"


class Message(BaseModel):
    id: int | None = None
    recipient: str = Field(..., min_length=1, max_length=255, description="Recipient (email/phone/username)")
    content: str = Field(..., min_length=1, max_length=2000)
    status: MessageStatus = MessageStatus.pending
    channel: ChannelType = ChannelType.email
    scheduled_for: str | None = None
    error: str | None = None

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

    @field_validator("scheduled_for", mode="before")
    def _validate_scheduled_for(cls, v: str | None) -> str | None:
        if v is None:
            return None
        vv = (v or "").strip()
        if not vv:
            return None
        try:
            _parse_dt(vv)
        except Exception:
            raise ValueError("scheduled_for must be ISO 8601, e.g. 2025-12-31T23:59:59Z")
        return vv


class Campaign(BaseModel):
    id: int | None = None
    title: str = Field(..., min_length=1, max_length=255)
    description: str | None = Field(None, max_length=2000)
    active: bool = True
    archived: bool = False
    company_id: int | None = None
    tags: list[str] = Field(default_factory=list)
    messages: list[Message] = Field(default_factory=list)
    created_at: str | None = None
    updated_at: str | None = None
    schedule: str | None = None
    owner: str | None = Field(None, max_length=100)
    processing_status: str | None = None
    queued_at: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    failed_at: str | None = None
    next_attempt_at: str | None = None
    last_error: str | None = None
    attempts: int | None = None
    request_id: str | None = None

    @field_validator("tags", mode="before")
    def _ensure_tags(cls, v):
        if v is None:
            return []
        if isinstance(v, str):
            v = [v]
        cleaned: list[str] = []
        seen = set()
        for raw in v or []:
            tag = (raw or "").strip().lower()
            if not tag:
                continue
            if len(tag) > 50:
                raise ValueError("tag must be <= 50 characters")
            if tag in seen:
                continue
            seen.add(tag)
            cleaned.append(tag)
        return cleaned

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

    @field_validator("owner", mode="before")
    def _normalize_owner(cls, v: str | None) -> str | None:
        if v is None:
            return None
        vv = (v or "").strip()
        return vv or None

    @field_validator("schedule", mode="before")
    def _validate_schedule(cls, v: str | None) -> str | None:
        if v is None:
            return None
        vv = (v or "").strip()
        if not vv:
            return None
        try:
            _parse_dt(vv)
        except Exception:
            raise ValueError("schedule must be ISO 8601, e.g. 2025-12-31T23:59:59Z")
        return vv


class CampaignStats(BaseModel):
    total_messages: int
    pending: int
    sent: int
    delivered: int
    failed: int
    read: int
    scheduled: int
    cancelled: int


class ScheduleRequest(BaseModel):
    schedule_time: str = Field(..., description="Scheduled time (ISO 8601, UTC or with TZ)")

    @field_validator("schedule_time")
    def validate_schedule_time(cls, v: str) -> str:
        try:
            dt = _parse_dt(v)
        except Exception:
            raise ValueError("schedule_time must be ISO 8601, e.g. 2025-12-31T23:59:59Z")
        if dt <= _utcnow():
            raise ValueError("schedule_time must be in the future")
        return v


class AddTagRequest(BaseModel):
    tag: str = Field(..., min_length=1, max_length=50, description="New tag")


class SetTagsRequest(BaseModel):
    tags: list[str] = Field(default_factory=list)


class PageMeta(BaseModel):
    page: int
    size: int
    total: int


class CampaignListResponse(BaseModel):
    items: list[Campaign]
    meta: PageMeta


class ArchiveRequest(BaseModel):
    reason: str | None = Field(None, description="Archive reason")


class RestoreRequest(BaseModel):
    reason: str | None = Field(None, description="Restore reason")


class BulkDeleteRequest(BaseModel):
    ids: list[int] = Field(default_factory=list)


class UpsertMessageRequest(BaseModel):
    recipient: str = Field(..., min_length=1)
    content: str = Field(..., min_length=1, max_length=2000)
    status: MessageStatus = MessageStatus.pending
    channel: ChannelType = ChannelType.email


class UpdateMessageStatusRequest(BaseModel):
    status: MessageStatus
    error: str | None = None


class BulkStatusUpdateRequest(BaseModel):
    status: MessageStatus
    ids: list[int] = Field(default_factory=list)


class CampaignExportFormat(str, Enum):
    json = "json"
    csv = "csv"


class BulkMessageAddRequest(BaseModel):
    messages: list[UpsertMessageRequest]


class BulkUpsertMessageRequest(BaseModel):
    items: list[UpsertMessageRequest] = Field(default_factory=list)
