from __future__ import annotations

import json
import time

import pytest

from app.core.config import settings
from app.core.provider_registry import ProviderRegistry
from app.services import background_tasks
from app.services.otp_providers import OtpProviderResolver
from app.services.payment_providers import PaymentProviderResolver
from app.utils.pii import mask_email, mask_phone


def test_stub_tasks_fail_in_prod_without_provider(monkeypatch):
    monkeypatch.setattr(settings, "ENVIRONMENT", "production")
    monkeypatch.setattr(time, "sleep", lambda *_args, **_kwargs: None)

    assert background_tasks.send_sms_otp("+77001234567", "1234") is False
    assert background_tasks.send_email_notification("user@example.com", "Sub", "Body") is False
    assert (
        background_tasks.process_image_upload("https://example.com/a.jpg", 1)["error"]
        == "media_provider_not_configured"
    )
    assert background_tasks.sync_product_to_kaspi(42) is False
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


@pytest.mark.asyncio
async def test_env_endpoint_redacts_database_url(async_client, monkeypatch):
    monkeypatch.setenv("ALLOW_ENV_ENDPOINT", "1")
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@localhost:5432/smartsell")

    resp = await async_client.get("/env")
    assert resp.status_code == 200

    payload = resp.json()
    raw = "postgresql://user:pass@localhost:5432/smartsell"
    assert raw not in json.dumps(payload)
    assert payload.get("env", {}).get("DATABASE_URL") not in (None, raw)


@pytest.mark.asyncio
async def test_request_otp_returns_503_in_prod(async_client, monkeypatch):
    monkeypatch.setattr(settings, "ENVIRONMENT", "production")
    OtpProviderResolver.reset_cache()

    async def _no_provider(*_args, **_kwargs):
        return None

    monkeypatch.setattr(ProviderRegistry, "get_active_provider", _no_provider)

    resp = await async_client.post(
        "/api/v1/auth/request-otp",
        json={"phone": "+77001234567", "purpose": "login"},
    )

    assert resp.status_code == 503, resp.text
    assert resp.json().get("detail") == "otp_provider_not_configured"


@pytest.mark.asyncio
async def test_request_otp_noop_in_dev(async_client, monkeypatch):
    monkeypatch.setattr(settings, "ENVIRONMENT", "development")
    OtpProviderResolver.reset_cache()

    async def _no_provider(*_args, **_kwargs):
        return None

    monkeypatch.setattr(ProviderRegistry, "get_active_provider", _no_provider)

    resp = await async_client.post(
        "/api/v1/auth/request-otp",
        json={"phone": "+77001234567", "purpose": "login"},
    )

    assert resp.status_code == 200, resp.text
    data = resp.json().get("data") or {}
    assert data.get("provider") in {None, "noop"} or data.get("provider") == "noop"
    assert "dev_code" in data


@pytest.mark.asyncio
async def test_payment_intent_returns_503_in_prod(async_client, company_a_manager_headers, monkeypatch):
    monkeypatch.setattr(settings, "ENVIRONMENT", "production")
    PaymentProviderResolver.reset_cache()

    async def _no_provider(*_args, **_kwargs):
        return None

    monkeypatch.setattr(ProviderRegistry, "get_active_provider", _no_provider)

    resp = await async_client.post(
        "/api/v1/payments/intents",
        headers=company_a_manager_headers,
        json={
            "amount": "10.00",
            "currency": "KZT",
            "customer_id": "cust-1",
            "metadata": {},
        },
    )

    assert resp.status_code == 503, resp.text
    assert resp.json().get("detail") == "payment_provider_not_configured"
