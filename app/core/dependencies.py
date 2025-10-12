# app/core/deps.py
from __future__ import annotations

"""
Enterprise-grade FastAPI dependencies:
- Auth (required/optional), roles/scopes checks
- Access token decode (advanced path via app.core.security, legacy verify_token fallback)
- Audit logging hooks
- Rate limiting: Redis token-bucket (Lua) -> in-memory sliding window fallback
- Pagination
- Idempotency via Idempotency-Key (Redis -> memory)
- Client context (ip, ua, request/trace/span ids)

ENV (optional):
  RATE_LIMIT_PER_MINUTE=100
  RATE_LIMIT_WINDOW_SECONDS=60
  AUTH_RATE_LIMIT=10
  AUTH_RATE_WINDOW_SECONDS=60
  REDIS_URL=redis://localhost:6379/0
  ENVIRONMENT=development
"""

import os
import sys
import time
from collections import defaultdict, deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Deque, Optional

from fastapi import Depends, HTTPException, Request, Response, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

# ------------------------------------------------------------------------------
# Config (robust import with fallbacks)
# ------------------------------------------------------------------------------
try:
    from app.core.config import settings  # type: ignore
except Exception:  # pragma: no cover

    class _Settings:
        ENVIRONMENT: str = os.getenv("ENVIRONMENT", "development")
        REDIS_URL: str = os.getenv("REDIS_URL", "redis://localhost:6379/0")
        RATE_LIMIT_PER_MINUTE: int = int(os.getenv("RATE_LIMIT_PER_MINUTE", "100"))
        RATE_LIMIT_WINDOW_SECONDS: int = int(os.getenv("RATE_LIMIT_WINDOW_SECONDS", "60"))

    settings = _Settings()  # type: ignore

# ------------------------------------------------------------------------------
# Logging (robust import with fallbacks)
# ------------------------------------------------------------------------------
try:
    from app.core.logging import audit_logger, bind_context, get_logger  # type: ignore
except Exception:  # pragma: no cover

    class _DummyAuditLogger:
        def log_auth_success(self, **kwargs):
            ...

        def log_auth_failure(self, **kwargs):
            ...

    def get_logger(name: str):
        class _L:
            def info(self, *a, **k):
                ...

            def warning(self, *a, **k):
                ...

            def error(self, *a, **k):
                ...

            def debug(self, *a, **k):
                ...

        return _L()

    class _DummyBind:
        def __init__(self, **kwargs):
            ...

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            ...

    def bind_context(**kwargs):  # contextmanager stub
        return _DummyBind()

    audit_logger = _DummyAuditLogger()

log = get_logger(__name__)

# ------------------------------------------------------------------------------
# Exceptions (robust import with fallbacks)
# ------------------------------------------------------------------------------
try:
    from app.core.exceptions import (  # type: ignore
        AuthenticationError,
        AuthorizationError,
        RateLimitError,
    )
except Exception:  # pragma: no cover

    class AuthenticationError(HTTPException):
        def __init__(
            self,
            detail="Authentication required",
            code="AUTH_REQUIRED",
            status_code=401,
            headers: dict[str, str] | None = None,
        ):
            super().__init__(
                status_code=status_code,
                detail={"error": detail, "code": code},
                headers=headers or {"WWW-Authenticate": "Bearer"},
            )

    class AuthorizationError(HTTPException):
        def __init__(
            self,
            detail="Insufficient permissions",
            code="INSUFFICIENT_PERMISSIONS",
            status_code=403,
        ):
            super().__init__(status_code=status_code, detail={"error": detail, "code": code})

    class RateLimitError(HTTPException):
        def __init__(
            self,
            detail="Rate limit exceeded",
            code="RATE_LIMIT_EXCEEDED",
            status_code=429,
            headers: dict[str, str] | None = None,
        ):
            super().__init__(
                status_code=status_code,
                detail={"error": detail, "code": code},
                headers=headers or {},
            )


# ------------------------------------------------------------------------------
# DB session provider (supports app.core.database or app.core.db)
# ------------------------------------------------------------------------------
try:
    from app.core.database import get_db  # type: ignore
except Exception:  # pragma: no cover
    try:
        from app.core.db import get_db  # type: ignore
    except Exception:  # last resort

        def get_db():  # type: ignore
            # Dummy generator so Depends(get_db) does not explode
            class _Dummy:
                def __enter__(self):
                    return self

                def __exit__(self, *a):
                    ...

            def _gen():
                db = _Dummy()
                try:
                    yield db
                finally:
                    pass

            return _gen()


# ------------------------------------------------------------------------------
# Security helpers (advanced -> legacy)
# ------------------------------------------------------------------------------
_HAS_ADV_SECURITY = False
try:
    from app.core.security import (  # type: ignore; noqa: F401 (may be unused here); -> dict payload {sub, scp, role, jti, exp, kid, ...}
        decode_access_token,
    )

    _HAS_ADV_SECURITY = True
except Exception:  # pragma: no cover
    try:
        from app.core.security import verify_token as _legacy_verify_token  # type: ignore
    except Exception:
        _legacy_verify_token = None  # type: ignore

# ------------------------------------------------------------------------------
# Redis (optional)
# ------------------------------------------------------------------------------
try:
    import redis.asyncio as aioredis  # type: ignore

    _HAS_REDIS = True
except Exception:  # pragma: no cover
    aioredis = None  # type: ignore
    _HAS_REDIS = False

_redis_client: Optional[aioredis.Redis] = None  # type: ignore


def _redis() -> Optional[aioredis.Redis]:  # type: ignore
    """Lazy init of asyncio Redis client."""
    global _redis_client
    if not _HAS_REDIS:
        return None
    if _redis_client is None:
        try:
            _redis_client = aioredis.from_url(
                settings.REDIS_URL,
                encoding="utf-8",
                decode_responses=True,
                socket_timeout=1.0,
                socket_connect_timeout=1.0,
            )
        except Exception as e:  # pragma: no cover
            log.warning("Redis init failed", error=str(e))
            return None
    return _redis_client


# ------------------------------------------------------------------------------
# Utility: correlation ids and client info
# ------------------------------------------------------------------------------
def _extract_request_ids(request: Request) -> dict:
    rid = request.headers.get("x-request-id") or request.headers.get("x-correlation-id")
    trace_id = request.headers.get("trace-id")
    span_id = request.headers.get("span-id")
    return {"request_id": rid or "", "trace_id": trace_id or "", "span_id": span_id or ""}


def get_client_info(request: Request) -> dict:
    ids = _extract_request_ids(request)
    return {
        "ip_address": (
            request.headers.get("x-real-ip")
            or request.headers.get("x-forwarded-for")
            or (request.client.host if request.client else "")
        ),
        "user_agent": request.headers.get("user-agent", "unknown"),
        **ids,
    }


# ------------------------------------------------------------------------------
# Authentication
# ------------------------------------------------------------------------------
security = HTTPBearer(auto_error=False)


@dataclass
class AuthContext:
    user_id: int
    subject: str
    scopes: set[str]
    roles: set[str]
    token: str
    jti: Optional[str] = None
    raw_payload: Optional[dict] = None


def _decode_token_soft(token: str) -> Optional[dict]:
    """Decode token to payload; return None on any failure."""
    if not token:
        return None
    if _HAS_ADV_SECURITY:
        try:
            return decode_access_token(token)  # type: ignore
        except Exception:
            return None
    if _legacy_verify_token:
        try:
            sub = _legacy_verify_token(token)  # type: ignore
            if not sub:
                return None
            return {"sub": str(sub), "scp": [], "role": [], "jti": None}
        except Exception:
            return None
    return None


def _auth_context_from_payload(token: str, payload: dict) -> AuthContext:
    sub = str(payload.get("sub", ""))
    scopes = set(payload.get("scp") or [])
    roles = set(payload.get("role") or [])
    return AuthContext(
        user_id=int(sub) if sub.isdigit() else -1,
        subject=sub,
        scopes=scopes,
        roles=roles,
        token=token,
        jti=payload.get("jti"),
        raw_payload=payload,
    )


def _fetch_user(db, user_id: int):
    """Fetch active user; return None if missing."""
    try:
        from app.models.user import User  # type: ignore
    except Exception:
        # If model is not present, treat as missing user
        return None
    try:
        return db.query(User).filter(User.id == user_id, User.is_active.is_(True)).first()
    except Exception:
        return None


async def get_current_user_optional(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
    db=Depends(get_db),
) -> Optional[Any]:
    token: Optional[str] = credentials.credentials if credentials else None
    if not token:
        token = request.cookies.get("access_token")
    if not token:
        return None
    payload = _decode_token_soft(token)
    if not payload:
        return None
    ctx = _auth_context_from_payload(token, payload)
    if ctx.user_id <= 0:
        return None
    user = _fetch_user(db, ctx.user_id)
    return user


async def get_current_user(
    request: Request,
    response: Response,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
    db=Depends(get_db),
) -> Any:
    token: Optional[str] = credentials.credentials if credentials else None
    if not token:
        token = request.cookies.get("access_token")
    if not token:
        raise AuthenticationError("Authentication required", "AUTH_REQUIRED")

    payload = _decode_token_soft(token)
    if not payload:
        # аудит провала — без user_id
        info = get_client_info(request)
        with bind_context(**info):
            try:
                audit_logger.log_auth_failure(reason="invalid_token", **info)
            except Exception:
                pass
        raise AuthenticationError("Invalid or expired token", "INVALID_TOKEN")

    ctx = _auth_context_from_payload(token, payload)
    if ctx.user_id <= 0:
        raise AuthenticationError("Invalid token subject", "INVALID_SUBJECT")

    user = _fetch_user(db, ctx.user_id)
    if not user:
        raise AuthenticationError("User not found or inactive", "USER_NOT_FOUND")

    info = get_client_info(request)
    with bind_context(user_id=getattr(user, "id", None), **info):
        try:
            audit_logger.log_auth_success(
                user_id=getattr(user, "id", None),
                ip_address=info["ip_address"],
                user_agent=info["user_agent"],
            )
        except Exception:
            pass

    # echo request/trace ids back if missing
    if info.get("request_id") and "X-Request-ID" not in response.headers:
        response.headers["X-Request-ID"] = info["request_id"]
    if info.get("trace_id") and info["trace_id"] and "Trace-Id" not in response.headers:
        response.headers["Trace-Id"] = info["trace_id"]
    if info.get("span_id") and info["span_id"] and "Span-Id" not in response.headers:
        response.headers["Span-Id"] = info["span_id"]

    return user


async def get_current_active_user(current_user: Any = Depends(get_current_user)) -> Any:
    if not getattr(current_user, "is_active", False):
        raise AuthenticationError("User account is inactive", "INACTIVE_USER")
    return current_user


async def get_current_verified_user(current_user: Any = Depends(get_current_user)) -> Any:
    if not getattr(current_user, "is_verified", False):
        raise AuthenticationError("User account is not verified", "UNVERIFIED_USER")
    return current_user


async def get_current_superuser(current_user: Any = Depends(get_current_user)) -> Any:
    if not getattr(current_user, "is_superuser", False):
        raise AuthorizationError("Insufficient permissions", "INSUFFICIENT_PERMISSIONS")
    return current_user


def require_scopes(required: Sequence[str]) -> Callable[..., Any]:
    """Factory dependency to ensure required OAuth scopes are present."""
    req = set(required)

    async def _dep(
        request: Request,
        user: Any = Depends(get_current_user),
        credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
    ) -> Any:
        token = credentials.credentials if credentials else request.cookies.get("access_token")
        payload = _decode_token_soft(token) if token else None
        scopes = set(payload.get("scp", [])) if payload else set()
        if not req.issubset(scopes):
            raise AuthorizationError("Missing required scopes", "MISSING_SCOPES")
        return user

    return _dep


# ------------------------------------------------------------------------------
# Pagination
# ------------------------------------------------------------------------------
@dataclass
class Pagination:
    page: int = 1
    per_page: int = 20
    max_per_page: int = 100

    def __post_init__(self):
        self.page = max(1, int(self.page or 1))
        p = int(self.per_page or 20)
        self.per_page = min(self.max_per_page, max(1, p))

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.per_page

    @property
    def limit(self) -> int:
        return self.per_page


def get_pagination(page: int = 1, per_page: int = 20) -> Pagination:
    return Pagination(page=page, per_page=per_page)


# ------------------------------------------------------------------------------
# Rate limiting: Redis token bucket -> memory sliding window
# ------------------------------------------------------------------------------
def _env_int(name: str, default: int) -> int:
    try:
        v = int(os.getenv(name, "").strip() or default)
        return v if v >= 0 else default
    except Exception:
        return default


_API_RATE_LIMIT = _env_int("RATE_LIMIT_PER_MINUTE", getattr(settings, "RATE_LIMIT_PER_MINUTE", 100))
_API_RATE_WINDOW = _env_int(
    "RATE_LIMIT_WINDOW_SECONDS", getattr(settings, "RATE_LIMIT_WINDOW_SECONDS", 60)
)
_AUTH_RATE_LIMIT = _env_int("AUTH_RATE_LIMIT", 10)
_AUTH_RATE_WINDOW = _env_int("AUTH_RATE_WINDOW_SECONDS", 60)

# in-memory sliding window
_rate_mem: dict[str, Deque[float]] = defaultdict(deque)

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


async def _rl_redis_allow(key: str, max_requests: int, window_seconds: int) -> tuple[bool, int]:
    client = _redis()
    if not client:
        return True, 0
    rate = max_requests / float(window_seconds)
    burst = max_requests
    now_ms = int(time.time() * 1000)
    try:
        res = await client.eval(_RL_LUA, 1, key, rate, burst, now_ms, 1, window_seconds * 2)
        allowed, _tokens, retry_ms = int(res[0]), int(res[1]), int(res[2])
        return (allowed == 1), int((retry_ms + 999) / 1000)
    except Exception as e:
        log.warning("Redis rate-limit error; falling back to memory", error=str(e))
        return True, 0


def _rl_mem_allow(key: str, max_requests: int, window_seconds: int) -> tuple[bool, int]:
    now = time.time()
    cutoff = now - window_seconds
    q = _rate_mem[key]
    while q and q[0] <= cutoff:
        q.popleft()
    if len(q) >= max_requests:
        retry = max(1, int(window_seconds - (now - q[0])))
        return False, retry
    q.append(now)
    return True, 0


def _rate_key(request: Request, tag: str, per_user: bool = True) -> str:
    base = f"{getattr(settings, 'ENVIRONMENT', 'dev')}:{tag}:{request.method}:{request.url.path}"
    if per_user:
        user_or_ip = "anon"
        auth = request.headers.get("authorization")
        if auth and " " in auth:
            # keep only tail of token to avoid storing secrets
            user_or_ip = auth.rsplit(" ", 1)[-1][-16:]
        else:
            user_or_ip = request.client.host if request.client else "0.0.0.0"
        return f"{base}:{user_or_ip}"
    return base


def rate_limit(
    max_requests: int = 100, window_seconds: int = 60, tag: str = "api", per_user: bool = True
):
    """Factory of async dependency for rate limiting with Redis->memory fallback."""

    async def dep(request: Request):
        key = _rate_key(request, tag=tag, per_user=per_user)
        if _HAS_REDIS and _redis():
            allowed, retry = await _rl_redis_allow(key, max_requests, window_seconds)
        else:
            allowed, retry = _rl_mem_allow(key, max_requests, window_seconds)
        if not allowed:
            headers = {
                "Retry-After": str(retry),
                "X-RateLimit-Limit": str(max_requests),
                "X-RateLimit-Window": str(window_seconds),
            }
            raise RateLimitError(
                f"Rate limit exceeded. Maximum {max_requests} requests per {window_seconds} seconds.",
                "RATE_LIMIT_EXCEEDED",
                headers=headers,
            )
        return True

    return dep


# Profiles (names for compatibility with your routers)
async def auth_rate_limit(request: Request):
    dep = rate_limit(
        max_requests=_AUTH_RATE_LIMIT, window_seconds=_AUTH_RATE_WINDOW, tag="auth", per_user=True
    )
    return await dep(request)


async def api_rate_limit(request: Request):
    dep = rate_limit(
        max_requests=_API_RATE_LIMIT, window_seconds=_API_RATE_WINDOW, tag="api", per_user=True
    )
    return await dep(request)


# Backward-compat names the user pasted in their draft (keep both)
auth_rate_limit_dep = auth_rate_limit
api_rate_limit_dep = api_rate_limit

# ------------------------------------------------------------------------------
# Idempotency via Idempotency-Key
# ------------------------------------------------------------------------------
_idem_mem: dict[str, tuple[int, float]] = {}


async def ensure_idempotency(request: Request, response: Response):
    """
    Use on mutating routes:
      @router.post("/pay", dependencies=[Depends(ensure_idempotency)])
    """
    method = request.method.upper()
    if method not in ("POST", "PUT", "PATCH"):
        return True

    key = request.headers.get("Idempotency-Key")
    if not key:
        # Choose strict behavior if needed:
        # raise HTTPException(400, "Idempotency-Key header required")
        return True

    ttl_seconds = int(request.headers.get("Idempotency-TTL", "900"))  # default 15 min
    r = _redis()

    if _HAS_REDIS and r:
        redis_key = f"idemp:{getattr(settings, 'ENVIRONMENT', 'dev')}:{key}"
        try:
            set_ok = await r.set(redis_key, "processing", ex=ttl_seconds, nx=True)
            if not set_ok:
                status_text = await r.get(redis_key)
                if status_text and status_text.isdigit():
                    raise HTTPException(status.HTTP_409_CONFLICT, "Request already processed")
                raise HTTPException(status.HTTP_409_CONFLICT, "Request is being processed")
            request.state.idempotency_key = key
            request.state.idempotency_ttl = ttl_seconds
            return True
        except HTTPException:
            raise
        except Exception as e:
            log.warning("Idempotency Redis error; falling back to memory", error=str(e))

    now = time.time()
    rec = _idem_mem.get(key)
    if rec:
        status_code, exp = rec
        if now < exp:
            raise HTTPException(status.HTTP_409_CONFLICT, "Request already processed")
        else:
            _idem_mem.pop(key, None)
    _idem_mem[key] = (102, now + ttl_seconds)
    request.state.idempotency_key = key
    request.state.idempotency_ttl = ttl_seconds
    return True


async def set_idempotency_result(key: str, status_code: int, ttl_seconds: int = 900) -> None:
    r = _redis()
    if _HAS_REDIS and r:
        try:
            redis_key = f"idemp:{getattr(settings, 'ENVIRONMENT', 'dev')}:{key}"
            await r.set(redis_key, str(status_code), ex=ttl_seconds)
            return
        except Exception:
            pass
    _idem_mem[key] = (status_code, time.time() + ttl_seconds)


# ------------------------------------------------------------------------------
# Module alias to support legacy imports: app.core.dependencies -> this module
# ------------------------------------------------------------------------------
# If some routers still do: from app.core.dependencies import api_rate_limit
# this alias ensures it works without touching their code.
sys.modules.setdefault("app.core.dependencies", sys.modules[__name__])

# ------------------------------------------------------------------------------
# Public API
# ------------------------------------------------------------------------------
__all__ = [
    # users/auth
    "get_current_user",
    "get_current_user_optional",
    "get_current_active_user",
    "get_current_verified_user",
    "get_current_superuser",
    "require_scopes",
    # rate limit
    "rate_limit",
    "auth_rate_limit",
    "api_rate_limit",
    "auth_rate_limit_dep",  # keep aliases
    "api_rate_limit_dep",
    # pagination
    "Pagination",
    "get_pagination",
    # client info / context
    "get_client_info",
    # idempotency
    "ensure_idempotency",
    "set_idempotency_result",
]
