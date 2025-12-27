from __future__ import annotations

"""Centralized async Redis client builder.

Respects settings.redis_settings:
- url/password/db
- socket_timeout (also used for socket_connect_timeout)
- strict: if True, fail fast when client cannot be created.

Returns a singleton client or None (when strict is False and Redis is unavailable).
"""

import logging
from typing import Optional

from app.core.config import settings

try:  # pragma: no cover - optional dependency guard
    import redis.asyncio as aioredis  # type: ignore

    _HAS_REDIS_LIB = True
except Exception:  # pragma: no cover
    aioredis = None  # type: ignore
    _HAS_REDIS_LIB = False

log = logging.getLogger(__name__)
_client: Optional["aioredis.Redis"] = None  # type: ignore[name-defined]


def get_redis() -> Optional["aioredis.Redis"]:  # type: ignore[name-defined]
    cfg = getattr(settings, "redis_settings", {}) or {}
    url = cfg.get("url", getattr(settings, "REDIS_URL", "redis://localhost:6379"))
    password = cfg.get("password")
    db = cfg.get("db", getattr(settings, "REDIS_DB", 0))
    strict = bool(cfg.get("strict", False))
    socket_timeout = float(cfg.get("socket_timeout", 1.0))

    if not _HAS_REDIS_LIB:
        if strict:
            raise RuntimeError("Redis client required but redis library is missing")
        return None

    global _client
    if _client is None:
        try:
            _client = aioredis.from_url(  # type: ignore[attr-defined]
                url,
                password=password,
                db=db,
                encoding="utf-8",
                decode_responses=True,
                socket_timeout=socket_timeout,
                socket_connect_timeout=socket_timeout,
            )
        except Exception as exc:  # pragma: no cover - guarded by strict flag
            if strict:
                raise RuntimeError(f"Redis init failed: {exc}")
            log.warning("Redis init failed; falling back to in-memory", extra={"error": str(exc)})
            return None

    return _client


__all__ = ["get_redis"]
