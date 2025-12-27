from __future__ import annotations

import json
import os
from functools import lru_cache
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings

__all__ = ["encrypt_json", "decrypt_json", "reset_crypto_key_cache"]


def _master_key() -> bytes:
    """Resolve the master key from env/settings and validate."""
    key = os.getenv("INTEGRATIONS_MASTER_KEY") or getattr(settings, "INTEGRATIONS_MASTER_KEY", None)
    if not key:
        raise RuntimeError("INTEGRATIONS_MASTER_KEY is not configured")
    key_bytes = key.encode() if isinstance(key, str) else key
    try:
        # Fernet ctor validates key shape (base64-encoded 32 bytes)
        Fernet(key_bytes)
    except Exception as exc:  # pragma: no cover - defensive
        raise RuntimeError(
            "Invalid INTEGRATIONS_MASTER_KEY; must be a base64-encoded 32-byte key"
        ) from exc
    return key_bytes


@lru_cache(maxsize=1)
def _fernet() -> Fernet:
    return Fernet(_master_key())


def reset_crypto_key_cache() -> None:
    """Clear cached Fernet instance (used in tests when key changes)."""
    _fernet.cache_clear()


def encrypt_json(payload: dict[str, Any] | list[Any]) -> bytes:
    """Encrypt JSON-serializable payload using master key."""
    data = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return _fernet().encrypt(data)


def decrypt_json(token: bytes | str) -> dict[str, Any] | list[Any]:
    """Decrypt payload back to Python object."""
    token_bytes = token.encode() if isinstance(token, str) else token
    try:
        raw = _fernet().decrypt(token_bytes)
    except InvalidToken as exc:
        raise ValueError("Invalid encrypted payload") from exc
    return json.loads(raw.decode("utf-8"))
