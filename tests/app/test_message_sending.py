from __future__ import annotations

from sqlalchemy.orm import sessionmaker

import tests.conftest as base_conftest
from app.models.campaign import Campaign, CampaignStatus, ChannelType, Message, MessageStatus
from app.models.company import Company
from app.worker import scheduler_worker


def _seed_message(*, company_id: int, title_suffix: str) -> int:
    if base_conftest.sync_engine is None:
        raise RuntimeError("sync_engine is not initialized; ensure test_db fixture runs first")

    SessionLocal = sessionmaker(bind=base_conftest.sync_engine, expire_on_commit=False, autoflush=False)
    with SessionLocal() as s:
        company = s.query(Company).filter(Company.id == company_id).first()
        if not company:
            company = Company(id=company_id, name=f"Company {company_id}")
            s.add(company)
            s.flush()

        campaign = Campaign(
            title=f"Send {title_suffix}",
            description="test",
            status=CampaignStatus.READY,
            scheduled_at=None,
            company_id=company.id,
        )
        s.add(campaign)
        s.flush()

        message = Message(
            campaign_id=campaign.id,
            recipient="user@example.com",
            content="Hello",
            status=MessageStatus.PENDING,
            channel=ChannelType.EMAIL,
        )
        s.add(message)
        s.commit()
        s.refresh(message)
        return message.id


def test_send_message_idempotent(monkeypatch, test_db):
    _ = test_db
    message_id = _seed_message(company_id=9301, title_suffix="idempotent")

    call_count = {"count": 0}

    def _fake_send(_smtp, _msg):
        call_count["count"] += 1
        return "provider-123"

    monkeypatch.setattr(scheduler_worker, "_send_via_smtp", _fake_send)

    scheduler_worker.send_message(message_id)
    scheduler_worker.send_message(message_id)

    assert call_count["count"] == 1

    SessionLocal = sessionmaker(bind=base_conftest.sync_engine, expire_on_commit=False, autoflush=False)
    with SessionLocal() as s:
        message = s.query(Message).filter(Message.id == message_id).first()
        assert message is not None
        assert message.status == MessageStatus.SENT
        assert message.sent_at is not None
        assert message.error_message is None
        assert message.provider_message_id == "provider-123"


def test_send_message_failure_sets_failed(monkeypatch, test_db):
    _ = test_db
    message_id = _seed_message(company_id=9302, title_suffix="failed")

    def _boom(_smtp, _msg):
        raise RuntimeError("boom" + "x" * 1000)

    monkeypatch.setattr(scheduler_worker, "_send_via_smtp", _boom)

    scheduler_worker.send_message(message_id)

    SessionLocal = sessionmaker(bind=base_conftest.sync_engine, expire_on_commit=False, autoflush=False)
    with SessionLocal() as s:
        message = s.query(Message).filter(Message.id == message_id).first()
        assert message is not None
        assert message.status == MessageStatus.FAILED
        assert message.sent_at is None
        assert message.error_message
        assert "RuntimeError" in message.error_message
        assert len(message.error_message) <= scheduler_worker._ERROR_MESSAGE_LIMIT
