from __future__ import annotations

import hashlib
import hmac
import os
import time
from collections.abc import Mapping
from dataclasses import dataclass

TIPTOP_SIGN_MODE_RAW = "HMAC_SHA256_RAW_BODY"
TIPTOP_SIGN_MODE_TIMESTAMP_DOT_BODY = "HMAC_SHA256_TIMESTAMP_DOT_BODY"

_SIGN_MODE_ALIASES = {
    "raw": TIPTOP_SIGN_MODE_RAW,
    "hmac_sha256_raw_body": TIPTOP_SIGN_MODE_RAW,
    "timestamp_dot_body": TIPTOP_SIGN_MODE_TIMESTAMP_DOT_BODY,
    "hmac_sha256_timestamp_dot_body": TIPTOP_SIGN_MODE_TIMESTAMP_DOT_BODY,
}


@dataclass(frozen=True)
class TipTopWebhookVerificationError(Exception):
    detail: str
    status_code: int = 403


def resolve_tiptop_sign_mode(value: str | None) -> str:
    raw = (value or "").strip().lower()
    if not raw:
        return TIPTOP_SIGN_MODE_RAW
    return _SIGN_MODE_ALIASES.get(raw, TIPTOP_SIGN_MODE_RAW)


def _signature_payload(body: bytes, timestamp: str | None, mode: str) -> bytes:
    if mode == TIPTOP_SIGN_MODE_TIMESTAMP_DOT_BODY:
        if not timestamp:
            raise TipTopWebhookVerificationError("missing_timestamp", 403)
        return f"{timestamp}.".encode() + body
    return body


def sign_tiptop_webhook_payload(
    *,
    body: bytes,
    secret: str,
    timestamp: str | None,
    sign_mode: str | None = None,
) -> str:
    mode = resolve_tiptop_sign_mode(sign_mode)
    payload = _signature_payload(body, timestamp, mode)
    return hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()


def verify_tiptop_webhook_signature(
    *,
    body: bytes,
    headers: Mapping[str, str],
    secret: str | None,
    is_production: bool,
    sign_mode: str | None = None,
    now: float | None = None,
) -> None:
    # NOTE: TipTop signature verification uses HMAC-SHA256 (mode selectable).
    signature = headers.get("x-tiptop-signature") or headers.get("x-signature")
    if not signature:
        raise TipTopWebhookVerificationError("missing_signature", 403)

    if not secret:
        raise TipTopWebhookVerificationError("payment_provider_not_configured", 503)

    mode = resolve_tiptop_sign_mode(sign_mode or os.getenv("TIPTOP_WEBHOOK_SIGN_MODE"))

    ts_header = headers.get("x-tiptop-timestamp") or headers.get("x-timestamp")
    requires_timestamp = is_production or mode == TIPTOP_SIGN_MODE_TIMESTAMP_DOT_BODY
    if not ts_header:
        if requires_timestamp:
            raise TipTopWebhookVerificationError("missing_timestamp", 403)
        ts_header = None

    if ts_header is not None:
        try:
            ts = int(ts_header)
        except Exception:
            raise TipTopWebhookVerificationError("invalid_timestamp", 403)
        now_ts = int(now if now is not None else time.time())
        if abs(now_ts - ts) > 300:
            raise TipTopWebhookVerificationError("invalid_timestamp", 403)

    expected = sign_tiptop_webhook_payload(
        body=body,
        secret=secret,
        timestamp=ts_header,
        sign_mode=mode,
    )
    if not hmac.compare_digest(signature, expected):
        raise TipTopWebhookVerificationError("invalid_signature", 403)
