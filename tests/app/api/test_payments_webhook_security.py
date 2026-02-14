from __future__ import annotations

import hashlib
import hmac
import json
import time

import pytest
from sqlalchemy import select

from app.core.config import settings
from app.models.idempotency_key import IdempotencyKey

pytestmark = pytest.mark.asyncio


def _sign(payload: bytes, secret: str) -> str:
    return hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()


async def test_tiptop_webhook_invalid_signature(async_client, monkeypatch):
    monkeypatch.setattr(settings, "ENVIRONMENT", "development", raising=False)
    monkeypatch.setattr(settings, "TIPTOP_API_SECRET", "s1", raising=False)
    monkeypatch.setattr(settings, "TIPTOP_API_KEY", "k1", raising=False)

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


async def test_tiptop_webhook_valid_signature(async_client, monkeypatch):
    monkeypatch.setattr(settings, "ENVIRONMENT", "development", raising=False)
    monkeypatch.setattr(settings, "TIPTOP_API_SECRET", "s1", raising=False)
    monkeypatch.setattr(settings, "TIPTOP_API_KEY", "k1", raising=False)

    payload = {"event_id": "evt-ok-1", "company_id": 1001}
    body = json.dumps(payload).encode("utf-8")
    signature = _sign(body, "s1")

    resp = await async_client.post(
        "/api/v1/payments/webhooks/tiptop",
        content=body,
        headers={
            "X-TipTop-Signature": signature,
            "X-TipTop-Timestamp": str(int(time.time())),
            "Content-Type": "application/json",
        },
    )

    assert resp.status_code == 200, resp.text
    assert resp.json().get("ok") is True


async def test_tiptop_webhook_missing_secret_in_prod(async_client, monkeypatch):
    monkeypatch.setattr(settings, "ENVIRONMENT", "production", raising=False)
    monkeypatch.setattr(settings, "TIPTOP_API_SECRET", None, raising=False)
    monkeypatch.setattr(settings, "TIPTOP_API_KEY", None, raising=False)

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

    payload = {"event_id": "evt-dup-1", "company_id": 1001}
    body = json.dumps(payload).encode("utf-8")
    signature = _sign(body, "s1")

    headers = {
        "X-TipTop-Signature": signature,
        "X-TipTop-Timestamp": str(int(time.time())),
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
