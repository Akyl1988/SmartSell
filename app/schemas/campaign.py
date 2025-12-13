from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models.campaign import CampaignStatus, MessageStatus


class MessageCreate(BaseModel):
    recipient: str
    content: str
    status: MessageStatus = MessageStatus.PENDING  # Explicit status specification


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
    title: str
    description: str | None = None
    scheduled_at: datetime | None = None
    messages: list[MessageCreate] = []


class CampaignUpdate(BaseModel):
    title: str | None = None
    description: str | None = None
    status: CampaignStatus | None = None
    scheduled_at: datetime | None = None


class CampaignResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    description: str | None = None
    status: CampaignStatus
    created_at: datetime
    updated_at: datetime
    scheduled_at: datetime | None = None
    messages: list[MessageResponse] = []
