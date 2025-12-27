from __future__ import annotations

import logging
import time
from typing import Optional

from fastapi import HTTPException, Request, Response, status

log = logging.getLogger(__name__)


class IdempotencyEnforcer:
    """Idempotency helper with Redis backend and memory fallback."""

    def __init__(
        self,
        redis=None,
        prefix: str = "idemp",
        default_ttl: int = 900,
        env: str = "dev",
    ) -> None:
        self.redis = redis
        self.prefix = prefix
        self.default_ttl = max(1, int(default_ttl or 1))
        self.env = env or "dev"
        self._mem: dict[str, tuple[int, float]] = {}

    def _key(self, key: str) -> str:
        return f"{self.env}:{self.prefix}:{key}"

    async def _reserve(self, key: str, ttl_seconds: int) -> tuple[bool, Optional[int]]:
        ttl = max(1, int(ttl_seconds))
        if self.redis is not None:
            try:
                redis_key = self._key(key)
                set_ok = await self.redis.set(redis_key, "processing", ex=ttl, nx=True)  # type: ignore[attr-defined]
                if set_ok:
                    return True, None
                status_text = await self.redis.get(redis_key)  # type: ignore[attr-defined]
                if status_text and str(status_text).isdigit():
                    return False, int(status_text)
                return False, None
            except Exception as exc:  # pragma: no cover - fallback path
                log.warning("Idempotency Redis error; falling back to memory", extra={"error": str(exc)})

        now = time.time()
        rec = self._mem.get(key)
        if rec:
            status_code, exp = rec
            if now < exp:
                return False, status_code
            self._mem.pop(key, None)
        self._mem[key] = (102, now + ttl)
        return True, None

    async def set_result(self, key: str, status_code: int, ttl_seconds: Optional[int] = None) -> None:
        ttl = max(1, int(ttl_seconds or self.default_ttl))
        if self.redis is not None:
            try:
                await self.redis.set(self._key(key), str(status_code), ex=ttl)  # type: ignore[attr-defined]
                return
            except Exception:  # pragma: no cover - fallback path
                pass
        self._mem[key] = (status_code, time.time() + ttl)

    def dependency(self, allow_replay: bool = False):
        async def dep(request: Request, response: Response):
            method = request.method.upper()
            if method not in ("POST", "PUT", "PATCH"):
                return True

            key = request.headers.get("Idempotency-Key")
            if not key:
                return True

            ttl_header = request.headers.get("Idempotency-TTL")
            try:
                ttl_seconds = int(ttl_header) if ttl_header else self.default_ttl
            except Exception:
                ttl_seconds = self.default_ttl

            allowed, processed_status = await self._reserve(key, ttl_seconds)
            if not allowed:
                if processed_status is not None and allow_replay:
                    # Request was already processed; allow handler to proceed and return same status
                    request.state.idempotency_key = key
                    request.state.idempotency_ttl = ttl_seconds
                    response.headers["Idempotency-Key"] = key
                    response.status_code = processed_status
                    return True
                detail = "Request already processed" if processed_status else "Request is being processed"
                raise HTTPException(status.HTTP_409_CONFLICT, detail)

            request.state.idempotency_key = key
            request.state.idempotency_ttl = ttl_seconds
            response.headers["Idempotency-Key"] = key
            return True

        return dep


def ensure_idempotency_dep(enforcer: IdempotencyEnforcer):
    return enforcer.dependency()


__all__ = ["IdempotencyEnforcer", "ensure_idempotency_dep"]
