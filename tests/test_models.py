import os

# Set testing environment variable
os.environ["TESTING"] = "1"

from enum import Enum


class CampaignStatus(Enum):
    DRAFT = "draft"
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"


class MessageStatus(Enum):
    PENDING = "pending"
    SENT = "sent"
    DELIVERED = "delivered"
    FAILED = "failed"


class Campaign:
    def __init__(self, title, description, status):
        self.title = title
        self.description = description
        self.status = status


class Message:
    def __init__(self, campaign_id, recipient, content, status):
        self.campaign_id = campaign_id
        self.recipient = recipient
        self.content = content
        self.status = status


def test_campaign_status_enum():
    """Test campaign status enum values"""
    assert CampaignStatus.DRAFT.value == "draft"
    assert CampaignStatus.ACTIVE.value == "active"
    assert CampaignStatus.PAUSED.value == "paused"
    assert CampaignStatus.COMPLETED.value == "completed"


def test_message_status_enum():
    """Test message status enum values"""
    assert MessageStatus.PENDING.value == "pending"
    assert MessageStatus.SENT.value == "sent"
    assert MessageStatus.DELIVERED.value == "delivered"
    assert MessageStatus.FAILED.value == "failed"


def test_campaign_creation():
    """Test campaign model creation"""
    campaign = Campaign(
        title="Test Campaign",
        description="Test Description",
        status=CampaignStatus.DRAFT,
    )

    assert campaign.title == "Test Campaign"
    assert campaign.description == "Test Description"
    assert campaign.status == CampaignStatus.DRAFT


def test_message_creation():
    """Test message model creation"""
    message = Message(
        campaign_id=1,
        recipient="test@example.com",
        content="Test message content",
        status=MessageStatus.PENDING,
    )

    assert message.campaign_id == 1
    assert message.recipient == "test@example.com"
    assert message.content == "Test message content"
    assert message.status == MessageStatus.PENDING
