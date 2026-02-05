"""Secure token utilities for invitation and password reset flows."""

from __future__ import annotations

import hmac
import os
import secrets
from hashlib import sha256
from typing import Optional

try:
    from app.core.config import settings  # type: ignore
except Exception:  # pragma: no cover

    class _Settings:  # type: ignore
        SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret")
        INVITE_TOKEN_SECRET = os.getenv("INVITE_TOKEN_SECRET")
        RESET_TOKEN_SECRET = os.getenv("RESET_TOKEN_SECRET")

    settings = _Settings()  # type: ignore


def _default_secret(preferred: Optional[str] = None) -> str:
    if preferred:
        return preferred
    if getattr(settings, "is_production", False):
        raise RuntimeError("token_secret_required_in_prod")
    return getattr(settings, "SECRET_KEY", "dev-secret")


def generate_token(length: int = 32) -> str:
    """Generate URL-safe random token (>= length bytes before encoding)."""

    # token_urlsafe uses base64; length argument is number of bytes.
    return secrets.token_urlsafe(max(32, length))


def hash_token(token: str, *, secret: Optional[str] = None) -> str:
    """Return HMAC-SHA256 hex digest of token with the given secret."""

    key = _default_secret(secret)
    digest = hmac.new(key.encode("utf-8"), token.encode("utf-8"), sha256).hexdigest()
    return digest


def constant_time_compare(val1: str, val2: str) -> bool:
    return hmac.compare_digest(val1, val2)
