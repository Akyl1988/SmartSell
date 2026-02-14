from __future__ import annotations

import json
import time

import pytest
from sqlalchemy import select

from app.core.config import settings
from app.integrations.providers.tiptop.webhook_security import (
    TIPTOP_SIGN_MODE_RAW,
    TIPTOP_SIGN_MODE_TIMESTAMP_DOT_BODY,
    sign_tiptop_webhook_payload,
)
from app.models.idempotency_key import IdempotencyKey

pytestmark = pytest.mark.asyncio


async def test_tiptop_webhook_invalid_signature(async_client, monkeypatch):
    monkeypatch.setattr(settings, "ENVIRONMENT", "development", raising=False)
    monkeypatch.setattr(settings, "TIPTOP_API_SECRET", "s1", raising=False)
    monkeypatch.setattr(settings, "TIPTOP_API_KEY", "k1", raising=False)
    monkeypatch.setenv("TIPTOP_WEBHOOK_SIGN_MODE", TIPTOP_SIGN_MODE_RAW)

    payload = {"event_id": "evt-1", "company_id": 1001}
    body = json.dumps(payload).encode("utf-8")

    resp = await async_client.post(
        "/api/v1/payments/webhooks/tiptop",
        content=body,
        headers={
            "X-TipTop-Signature": "bad-signature",
            "X-TipTop-Timestamp": str(int(time.time())),
            "Content-Type": "application/json",
        },
    )

    assert resp.status_code == 403
    assert resp.json().get("detail") == "invalid_signature"


@pytest.mark.parametrize(
    "sign_mode",
    [TIPTOP_SIGN_MODE_RAW, TIPTOP_SIGN_MODE_TIMESTAMP_DOT_BODY],
)
async def test_tiptop_webhook_valid_signature(async_client, monkeypatch, sign_mode):
    monkeypatch.setattr(settings, "ENVIRONMENT", "development", raising=False)
    monkeypatch.setattr(settings, "TIPTOP_API_SECRET", "s1", raising=False)
    monkeypatch.setattr(settings, "TIPTOP_API_KEY", "k1", raising=False)
    monkeypatch.setenv("TIPTOP_WEBHOOK_SIGN_MODE", sign_mode)

    payload = {"event_id": "evt-ok-1", "company_id": 1001}
    body = json.dumps(payload).encode("utf-8")
    ts = str(int(time.time()))
    signature = sign_tiptop_webhook_payload(
        body=body,
        secret="s1",
        timestamp=ts,
        sign_mode=sign_mode,
    )

    resp = await async_client.post(
        "/api/v1/payments/webhooks/tiptop",
        content=body,
        headers={
            "X-TipTop-Signature": signature,
            "X-TipTop-Timestamp": ts,
            "Content-Type": "application/json",
        },
    )

    assert resp.status_code == 200, resp.text
    assert resp.json().get("ok") is True


async def test_tiptop_webhook_missing_secret_in_prod(async_client, monkeypatch):
    monkeypatch.setattr(settings, "ENVIRONMENT", "production", raising=False)
    monkeypatch.setattr(settings, "TIPTOP_API_SECRET", None, raising=False)
    monkeypatch.setattr(settings, "TIPTOP_API_KEY", None, raising=False)
    monkeypatch.setenv("TIPTOP_WEBHOOK_SIGN_MODE", TIPTOP_SIGN_MODE_RAW)

    payload = {"event_id": "evt-2", "company_id": 1001}
    body = json.dumps(payload).encode("utf-8")

    resp = await async_client.post(
        "/api/v1/payments/webhooks/tiptop",
        content=body,
        headers={
            "X-TipTop-Signature": "sig",
            "Content-Type": "application/json",
        },
    )

    assert resp.status_code == 503
    assert resp.json().get("detail") == "payment_provider_not_configured"


async def test_tiptop_webhook_idempotent(async_client, monkeypatch, async_db_session):
    monkeypatch.setattr(settings, "ENVIRONMENT", "development", raising=False)
    monkeypatch.setattr(settings, "TIPTOP_API_SECRET", "s1", raising=False)
    monkeypatch.setattr(settings, "TIPTOP_API_KEY", "k1", raising=False)
    monkeypatch.setenv("TIPTOP_WEBHOOK_SIGN_MODE", TIPTOP_SIGN_MODE_RAW)

    payload = {"event_id": "evt-dup-1", "company_id": 1001}
    body = json.dumps(payload).encode("utf-8")
    ts = str(int(time.time()))
    signature = sign_tiptop_webhook_payload(
        body=body,
        secret="s1",
        timestamp=ts,
        sign_mode=TIPTOP_SIGN_MODE_RAW,
    )

    headers = {
        "X-TipTop-Signature": signature,
        "X-TipTop-Timestamp": ts,
        "Content-Type": "application/json",
    }

    first = await async_client.post(
        "/api/v1/payments/webhooks/tiptop",
        content=body,
        headers=headers,
    )
    assert first.status_code == 200, first.text
    assert first.json().get("ok") is True

    rows = (
        (
            await async_db_session.execute(
                select(IdempotencyKey).where(
                    IdempotencyKey.company_id == 1001,
                    IdempotencyKey.key == "tiptop:evt-dup-1",
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1

    second = await async_client.post(
        "/api/v1/payments/webhooks/tiptop",
        content=body,
        headers=headers,
    )
    assert second.status_code == 200, second.text
    assert second.json().get("duplicate") is True

    rows_after = (
        (
            await async_db_session.execute(
                select(IdempotencyKey).where(
                    IdempotencyKey.company_id == 1001,
                    IdempotencyKey.key == "tiptop:evt-dup-1",
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(rows_after) == 1
