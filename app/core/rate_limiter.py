from __future__ import annotations

import logging
import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request, status

try:  # pragma: no cover - optional import
    from app.core.exceptions import RateLimitError  # type: ignore
except Exception:  # pragma: no cover
    RateLimitError = None  # type: ignore

log = logging.getLogger(__name__)

_RL_LUA = r"""
local key        = KEYS[1]
local rate       = tonumber(ARGV[1])   -- tokens per second
local burst      = tonumber(ARGV[2])   -- bucket capacity
local now_ms     = tonumber(ARGV[3])   -- current time in ms
local cost       = tonumber(ARGV[4])   -- tokens cost (usually 1)
local ttl_sec    = tonumber(ARGV[5])   -- bucket ttl seconds

local data = redis.call('HMGET', key, 'tokens', 'ts')
local tokens = tonumber(data[1])
local ts     = tonumber(data[2])

if tokens == nil then
  tokens = burst
  ts = now_ms
else
  local delta = math.max(0, now_ms - ts)
  local refill = delta * (rate / 1000.0)
  tokens = math.min(burst, tokens + refill)
  ts = now_ms
end

local allowed = 0
local retry_after_ms = 0
if tokens >= cost then
  allowed = 1
  tokens = tokens - cost
else
  allowed = 0
  retry_after_ms = math.ceil((cost - tokens) / (rate / 1000.0))
end

redis.call('HMSET', key, 'tokens', tokens, 'ts', ts)
redis.call('EXPIRE', key, ttl_sec)

return {allowed, math.floor(tokens), retry_after_ms}
"""


class RateLimiter:
    """Redis-backed token bucket with in-memory fallback.

    - redis: optional async Redis client.
    - env: environment tag to namespace keys.
    - prefix: key prefix.
    """

    def __init__(self, redis=None, env: str = "dev", prefix: str = "rl") -> None:
        self.redis = redis
        self.env = env or "dev"
        self.prefix = prefix
        self._mem: dict[str, deque[float]] = defaultdict(deque)

    def _key(self, tag: str, ident: str) -> str:
        return f"{self.env}:{self.prefix}:{tag}:{ident}"

    def ident_from_request(self, request: Request, per_user: bool = True) -> str:
        if not per_user:
            return request.client.host if request.client else "0.0.0.0"
        auth = request.headers.get("authorization")
        if auth and " " in auth:
            return auth.rsplit(" ", 1)[-1][-16:]
        return request.client.host if request.client else "0.0.0.0"

    async def allow(self, tag: str, ident: str, max_requests: int, window_seconds: int) -> tuple[bool, int]:
        if max_requests <= 0:
            return False, window_seconds
        if window_seconds <= 0:
            window_seconds = 1

        if self.redis is not None:
            rate = max_requests / float(window_seconds)
            burst = max_requests
            now_ms = int(time.time() * 1000)
            try:
                res = await self.redis.eval(  # type: ignore[attr-defined]
                    _RL_LUA, 1, self._key(tag, ident), rate, burst, now_ms, 1, window_seconds * 2
                )
                allowed, _tokens, retry_ms = int(res[0]), int(res[1]), int(res[2])
                return (allowed == 1), int((retry_ms + 999) / 1000)
            except Exception as exc:  # pragma: no cover - fallback path
                log.warning("Redis rate-limit error; falling back to memory", extra={"error": str(exc)})

        return self._allow_mem(tag, ident, max_requests, window_seconds)

    def _allow_mem(self, tag: str, ident: str, max_requests: int, window_seconds: int) -> tuple[bool, int]:
        now = time.time()
        cutoff = now - window_seconds
        key = self._key(tag, ident)
        q = self._mem[key]
        while q and q[0] <= cutoff:
            q.popleft()
        if len(q) >= max_requests:
            retry = max(1, int(window_seconds - (now - q[0])))
            return False, retry
        q.append(now)
        return True, 0


def rate_limit_dependency(
    limiter: RateLimiter,
    tag: str,
    max_requests: int,
    window_seconds: int,
    per_user: bool = True,
):
    async def dep(request: Request):
        ident = limiter.ident_from_request(request, per_user=per_user)
        allowed, retry = await limiter.allow(tag, ident, max_requests, window_seconds)
        if not allowed:
            headers = {
                "Retry-After": str(retry),
                "X-RateLimit-Limit": str(max_requests),
                "X-RateLimit-Window": str(window_seconds),
            }
            if RateLimitError:
                raise RateLimitError(
                    f"Rate limit exceeded. Maximum {max_requests} requests per {window_seconds} seconds.",
                    "RATE_LIMIT_EXCEEDED",
                    headers=headers,
                )
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Rate limit exceeded",
                headers=headers,
            )
        return True

    return dep


__all__ = ["RateLimiter", "rate_limit_dependency"]
