from __future__ import annotations

import time

import pytest

from app.core.config import settings
from app.services import background_tasks
from app.utils.pii import mask_email, mask_phone


def test_stub_tasks_fail_in_prod_without_provider(monkeypatch):
    monkeypatch.setattr(settings, "ENVIRONMENT", "production")
    monkeypatch.setattr(time, "sleep", lambda *_args, **_kwargs: None)

    assert background_tasks.send_sms_otp("+77001234567", "1234") is False
    assert background_tasks.send_email_notification("user@example.com", "Sub", "Body") is False
    assert background_tasks.process_image_upload("https://example.com/a.jpg", 1)["error"] == "media_provider_not_configured"
    assert background_tasks.process_payment_webhook({"invoice_id": "inv-1"}) is False
    assert background_tasks.generate_daily_report()["error"] == "report_provider_not_configured"


def test_sms_otp_logs_masked_phone(monkeypatch, caplog):
    monkeypatch.setattr(settings, "ENVIRONMENT", "development")
    monkeypatch.setattr(time, "sleep", lambda *_args, **_kwargs: None)
    caplog.set_level("INFO")

    phone = "+77001234567"
    background_tasks.send_sms_otp(phone, "1234")

    assert phone not in caplog.text
    assert mask_phone(phone) in caplog.text


def test_email_logs_masked_email(monkeypatch, caplog):
    monkeypatch.setattr(settings, "ENVIRONMENT", "development")
    monkeypatch.setattr(time, "sleep", lambda *_args, **_kwargs: None)
    caplog.set_level("INFO")

    email = "user@example.com"
    background_tasks.send_email_notification(email, "Hello", "Body")

    assert email not in caplog.text
    assert mask_email(email) in caplog.text
