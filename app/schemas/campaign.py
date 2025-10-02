from datetime import datetime
from typing import Optional

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
    sent_at: Optional[datetime] = None
    error_message: Optional[str] = None


class CampaignCreate(BaseModel):
    title: str
    description: Optional[str] = None
    scheduled_at: Optional[datetime] = None
    messages: list[MessageCreate] = []


class CampaignUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    status: Optional[CampaignStatus] = None
    scheduled_at: Optional[datetime] = None


class CampaignResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    description: Optional[str] = None
    status: CampaignStatus
    created_at: datetime
    updated_at: datetime
    scheduled_at: Optional[datetime] = None
    messages: list[MessageResponse] = []
