from __future__ import annotations

"""Centralized async Redis client builder.

Respects settings.redis_settings:
- url/password/db
- socket_timeout (also used for socket_connect_timeout)
- strict: if True, fail fast when client cannot be created.

Returns a singleton client or None (when strict is False and Redis is unavailable).
"""

import logging
import os
from typing import Optional

from app.core.config import settings

log = logging.getLogger(__name__)
_client = None


def _redis_disabled() -> bool:
    return bool(
        getattr(settings, "is_testing", False)
        or os.getenv("PYTEST_CURRENT_TEST")
        or os.getenv("TESTING", "").lower() in {"1", "true", "yes", "on"}
        or os.getenv("TEST_REDIS_DISABLED", "").lower() in {"1", "true", "yes", "on"}
        or os.getenv("FORCE_INMEMORY_BACKENDS", "").lower() in {"1", "true", "yes", "on"}
    )


def get_redis() -> Optional[object]:
    if _redis_disabled():
        return None

    cfg = getattr(settings, "redis_settings", {}) or {}
    url = cfg.get("url", getattr(settings, "REDIS_URL", "redis://localhost:6379"))
    password = cfg.get("password")
    db = cfg.get("db", getattr(settings, "REDIS_DB", 0))
    strict = bool(cfg.get("strict", False))
    socket_timeout = float(cfg.get("socket_timeout", 1.0))

    try:  # pragma: no cover - optional dependency guard
        import redis.asyncio as aioredis  # type: ignore
    except Exception:  # pragma: no cover
        aioredis = None  # type: ignore
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
