# app/core/deps.py
from __future__ import annotations

"""
Enterprise-grade FastAPI dependencies:
- Auth (required/optional), roles/scopes checks
- Access token decode (advanced path via app.core.security, legacy verify_token fallback)
- Audit logging hooks
- Rate limiting: Redis token-bucket (Lua) -> in-memory sliding window fallback
- Pagination
- Idempotency via Idempotency-Key (PostgreSQL)
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
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from fastapi import Depends, HTTPException, Request, Response, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select

from app.core.rbac import is_platform_admin, is_store_admin

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
# Auth DB dependency: prefer async db when available
# ------------------------------------------------------------------------------
try:
    from app.core.db import get_async_db  # type: ignore
except Exception:  # pragma: no cover
    get_async_db = None  # type: ignore


async def _get_auth_db():
    """Yield DB session; prefer async session when available."""

    if get_async_db:
        res = get_async_db()
        if hasattr(res, "__aiter__"):
            async for db in res:  # type: ignore[misc]
                yield db
            return
        if hasattr(res, "__await__"):
            db = await res  # type: ignore[misc]
            yield db
            return
        yield res
        return

    # Fallback: sync generator from get_db
    for db in get_db():
        yield db


# ------------------------------------------------------------------------------
# Security helpers (advanced -> legacy)
# ------------------------------------------------------------------------------
_HAS_ADV_SECURITY = False
try:
    from app.core.security import (  # type: ignore; noqa: F401 (may be unused here); -> dict payload {sub, scp, role, jti, exp, kid, ...}
        decode_and_validate,
        denylist_key_for_token,
        is_token_revoked,
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
from app.core.idempotency import IdempotencyEnforcer
from app.core.rate_limiter import RateLimiter, rate_limit_dependency
from app.core.redis_client import get_redis


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
    jti: str | None = None
    raw_payload: dict | None = None


def _decode_token_soft(token: str) -> dict | None:
    """Decode token to payload; return None on any failure."""
    if not token:
        return None
    if _HAS_ADV_SECURITY:
        try:
            return decode_and_validate(token, expected_type="access")  # type: ignore
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


async def _fetch_user(db, user_id: int):
    """Fetch active user; return None if missing; log unexpected DB issues."""
    try:
        from app.models.user import User  # type: ignore
    except Exception as exc:  # pragma: no cover - import failure should surface
        log.error("Failed to import User model", exc_info=exc)
        raise

    from sqlalchemy.ext.asyncio import AsyncSession as _AsyncSession  # type: ignore

    try:
        if isinstance(db, _AsyncSession):
            res = await db.execute(select(User).where(User.id == user_id, User.is_active.is_(True)))
            return res.scalars().first()

        if hasattr(db, "execute"):
            res = db.execute(select(User).where(User.id == user_id, User.is_active.is_(True)))
            return res.scalars().first() if hasattr(res, "scalars") else None

        raise TypeError(f"Unsupported db type for _fetch_user: {type(db)!r}")
    except Exception as exc:
        log.error("Failed to fetch user", exc_info=exc)
        raise


async def get_current_user_optional(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
    db=Depends(_get_auth_db),
) -> Any | None:
    token: str | None = credentials.credentials if credentials else None
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
    user = await _fetch_user(db, ctx.user_id)
    return user


async def get_current_user(
    request: Request,
    response: Response,
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
    db=Depends(_get_auth_db),
) -> Any:
    token: str | None = credentials.credentials if credentials else None
    if not token:
        token = request.cookies.get("access_token")
    if not token:
        raise AuthenticationError("Authentication required", "AUTH_REQUIRED")

    payload = None
    if _HAS_ADV_SECURITY:
        try:
            payload = decode_and_validate(token, expected_type="access")  # type: ignore
            key = denylist_key_for_token(token, payload)
            if key and is_token_revoked(key):
                raise AuthenticationError("Invalid or expired token", "INVALID_TOKEN")
        except ValueError as exc:
            if str(exc) == "Token expired":
                raise AuthenticationError("token_expired", "TOKEN_EXPIRED")
            raise AuthenticationError("Invalid or expired token", "INVALID_TOKEN")
    else:
        payload = _decode_token_soft(token)

    if not payload:
        # аудит провала — без user_id
        info = get_client_info(request)
        bind_context(**info)
        try:
            audit_logger.log_auth_failure(reason="invalid_token", **info)
        except Exception:
            pass
        raise AuthenticationError("Invalid or expired token", "INVALID_TOKEN")

    ctx = _auth_context_from_payload(token, payload)
    if ctx.user_id <= 0:
        raise AuthenticationError("Invalid token subject", "INVALID_SUBJECT")

    user = await _fetch_user(db, ctx.user_id)
    if not user:
        raise AuthenticationError("User not found or inactive", "USER_NOT_FOUND")

    info = get_client_info(request)
    bind_context(user_id=getattr(user, "id", None), **info)
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


async def require_active_subscription(
    request: Request,
    current_user: Any | None = Depends(get_current_user_optional),
    db=Depends(_get_auth_db),
) -> Any:
    path = (request.url.path or "").lower()
    if "/health" in path or "/_debug" in path:
        return current_user
    if path.startswith("/api/admin/integrations") or path.startswith("/api/v1/auth"):
        return current_user
    if path.startswith("/api/v1/wallet") or path.startswith("/api/v1/payments"):
        return current_user
    if path.startswith("/api/v1/subscriptions"):
        return current_user

    if current_user is None:
        return None

    from app.core.security import resolve_tenant_company_id  # type: ignore
    from app.core.subscriptions.errors import build_subscription_required_payload  # type: ignore
    from app.services.subscriptions import get_company_subscription, is_subscription_active  # type: ignore

    if is_platform_admin(current_user):
        return current_user

    company_id = resolve_tenant_company_id(current_user, not_found_detail="Company not set")
    subscription = await get_company_subscription(db, company_id)
    if not is_subscription_active(subscription):
        payload = await build_subscription_required_payload(db, current_user)
        raise HTTPException(status_code=402, detail=payload)
    return current_user


async def get_current_superuser(current_user: Any = Depends(get_current_user)) -> Any:
    if not getattr(current_user, "is_superuser", False):
        raise AuthorizationError("Insufficient permissions", "INSUFFICIENT_PERMISSIONS")
    return current_user


async def require_platform_admin(current_user: Any = Depends(get_current_user)) -> Any:
    if not is_platform_admin(current_user):
        raise AuthorizationError("Admin role required", "ADMIN_REQUIRED")
    return current_user


def require_roles_strict(*roles: str) -> Callable[..., Any]:
    allowed = {r.lower() for r in roles if r}

    async def _dep(current_user: Any = Depends(get_current_user)) -> Any:
        role = (getattr(current_user, "role", "") or "").lower()
        if role not in allowed:
            raise AuthorizationError("Insufficient role", "FORBIDDEN")
        return current_user

    return _dep


def require_store_roles(*roles: str) -> Callable[..., Any]:
    allowed = {r.lower() for r in roles if r}

    async def _dep(current_user: Any = Depends(get_current_user)) -> Any:
        if is_platform_admin(current_user):
            return current_user
        role = (getattr(current_user, "role", "") or "").lower()
        if role not in allowed:
            raise AuthorizationError("Insufficient role", "FORBIDDEN")
        return current_user

    return _dep


async def require_store_admin(current_user: Any = Depends(get_current_user)) -> Any:
    if not (is_store_admin(current_user) or is_platform_admin(current_user)):
        raise AuthorizationError("Admin role required", "ADMIN_REQUIRED")
    return current_user


def require_scopes(required: Sequence[str]) -> Callable[..., Any]:
    """Factory dependency to ensure required OAuth scopes are present."""
    req = set(required)

    async def _dep(
        request: Request,
        user: Any = Depends(get_current_user),
        credentials: HTTPAuthorizationCredentials | None = Depends(security),
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


def _int_setting(val, default: int) -> int:
    try:
        return int(val)
    except Exception:
        return default


_rate_cfg = getattr(settings, "rate_limit_settings", {}) or {}
_env_tag = getattr(settings, "ENVIRONMENT", "dev")
_RATE_ENABLED = bool(_rate_cfg.get("enabled", getattr(settings, "RATE_LIMIT_ENABLED", True)))

_API_RATE_LIMIT = _int_setting(_rate_cfg.get("api_per_minute", getattr(settings, "RATE_LIMIT_PER_MINUTE", 100)), 100)
_API_RATE_WINDOW = _int_setting(
    _rate_cfg.get("api_window_seconds", getattr(settings, "RATE_LIMIT_WINDOW_SECONDS", 60)), 60
)
_AUTH_RATE_LIMIT = _int_setting(_rate_cfg.get("auth_per_minute", 10), 10)
_AUTH_RATE_WINDOW = _int_setting(_rate_cfg.get("auth_window_seconds", 60), 60)
_OTP_RATE_LIMIT = _int_setting(_rate_cfg.get("otp_per_minute", 5), 5)
_OTP_RATE_WINDOW = _int_setting(_rate_cfg.get("otp_window_seconds", 60), 60)

_rate_limiter = RateLimiter(redis=get_redis(), env=_env_tag, prefix="rl") if _RATE_ENABLED else None


def _limit_dep(tag: str, max_requests: int, window_seconds: int, per_user: bool = True):
    if not _RATE_ENABLED:

        async def _noop(request: Request):
            return True

        return _noop

    return rate_limit_dependency(
        _rate_limiter,
        tag=tag,
        max_requests=max_requests,
        window_seconds=window_seconds,
        per_user=per_user,
    )


def rate_limit(max_requests: int = 100, window_seconds: int = 60, tag: str = "api", per_user: bool = True):
    dep = _limit_dep(tag, max_requests, window_seconds, per_user)

    async def _wrapped(request: Request):
        return await dep(request)

    return _wrapped


async def enforce_rate_limit(
    *,
    tag: str,
    ident: str,
    max_requests: int,
    window_seconds: int,
    detail: str,
):
    if not _RATE_ENABLED or not _rate_limiter:
        return True
    allowed, retry = await _rate_limiter.allow(tag, ident, max_requests, window_seconds)
    if not allowed:
        headers = {
            "Retry-After": str(retry),
            "X-RateLimit-Limit": str(max_requests),
            "X-RateLimit-Window": str(window_seconds),
        }
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=detail, headers=headers)
    return True


_auth_rl = _limit_dep("auth", _AUTH_RATE_LIMIT, _AUTH_RATE_WINDOW)
_api_rl = _limit_dep("api", _API_RATE_LIMIT, _API_RATE_WINDOW)
_otp_rl = _limit_dep("otp", _OTP_RATE_LIMIT, _OTP_RATE_WINDOW)


async def auth_rate_limit(request: Request):
    return await _auth_rl(request)


async def api_rate_limit(request: Request):
    return await _api_rl(request)


async def otp_rate_limit(request: Request):
    return await _otp_rl(request)


# Backward-compat names
auth_rate_limit_dep = auth_rate_limit
api_rate_limit_dep = api_rate_limit

# ------------------------------------------------------------------------------
# Idempotency via Idempotency-Key (PostgreSQL)
# ------------------------------------------------------------------------------
_idem_cfg = getattr(settings, "idempotency_settings", {}) or {}
_idem_default_ttl = _int_setting(_idem_cfg.get("default_ttl", getattr(settings, "IDEMPOTENCY_DEFAULT_TTL", 900)), 900)

_idempotency_enforcer = IdempotencyEnforcer(default_ttl=_idem_default_ttl, env=_env_tag)


async def _get_idempotency_db():
    if not get_async_db:
        raise RuntimeError("Async DB session is required for idempotency")
    async for db in get_async_db():
        yield db


def _resolve_idempotency_scope(current_user: Any | None, key: str) -> tuple[int | None, str]:
    if current_user is None:
        return None, key

    try:
        from app.core.security import resolve_tenant_company_id  # type: ignore

        company_id = resolve_tenant_company_id(current_user, not_found_detail="Company not set")
        company_id_int = int(company_id)
        scoped_key = key
        prefix = f"company:{company_id_int}:"
        if not scoped_key.startswith(prefix):
            scoped_key = f"{prefix}{key}"
        return company_id_int, scoped_key
    except Exception:
        pass

    user_id = getattr(current_user, "id", None)
    if user_id:
        return 0, f"user:{int(user_id)}:{key}"
    return None, key


async def _apply_idempotency(
    request: Request,
    response: Response,
    current_user: Any | None,
    db,
    *,
    allow_replay: bool,
):
    method = request.method.upper()
    if method not in ("POST", "PUT", "PATCH"):
        return True

    raw_key = request.headers.get("Idempotency-Key")
    if not raw_key:
        return True

    redis_client = get_redis()
    if settings.is_production:
        if redis_client is None:
            raise HTTPException(status_code=503, detail="idempotency_unavailable")
        try:
            await redis_client.ping()  # type: ignore[attr-defined]
        except Exception:
            raise HTTPException(status_code=503, detail="idempotency_unavailable")

    ttl_header = request.headers.get("Idempotency-TTL")
    try:
        ttl_seconds = int(ttl_header) if ttl_header else _idem_default_ttl
    except Exception:
        ttl_seconds = _idem_default_ttl

    company_id, scoped_key = _resolve_idempotency_scope(current_user, raw_key)
    if company_id is None:
        return True

    try:
        allowed, processed_status = await _idempotency_enforcer.reserve(
            db, company_id=company_id, key=scoped_key, ttl_seconds=ttl_seconds
        )
    except Exception as exc:
        log.error("Idempotency DB error", extra={"error": str(exc)})
        raise HTTPException(status_code=503, detail="Idempotency storage unavailable")

    if not allowed:
        if processed_status is not None and allow_replay:
            request.state.idempotency_key = scoped_key
            request.state.idempotency_ttl = ttl_seconds
            request.state.idempotency_company_id = company_id
            response.headers["Idempotency-Key"] = raw_key
            response.status_code = processed_status
            return True
        detail = "Request already processed" if processed_status is not None else "Request is being processed"
        raise HTTPException(status.HTTP_409_CONFLICT, detail)

    request.state.idempotency_key = scoped_key
    request.state.idempotency_ttl = ttl_seconds
    request.state.idempotency_company_id = company_id
    response.headers["Idempotency-Key"] = raw_key
    return True


async def ensure_idempotency(
    request: Request,
    response: Response,
    current_user: Any | None = Depends(get_current_user_optional),
    db=Depends(_get_idempotency_db, use_cache=False),
):
    return await _apply_idempotency(request, response, current_user, db, allow_replay=False)


async def ensure_idempotency_replay(
    request: Request,
    response: Response,
    current_user: Any | None = Depends(get_current_user_optional),
    db=Depends(_get_idempotency_db, use_cache=False),
):
    return await _apply_idempotency(request, response, current_user, db, allow_replay=True)


async def set_idempotency_result(
    key: str,
    *,
    status_code: int,
    ttl_seconds: int | None = None,
    request: Request | None = None,
    company_id: int | None = None,
):
    scoped_company_id = company_id
    scoped_key = key
    if request is not None:
        scoped_key = getattr(getattr(request, "state", None), "idempotency_key", scoped_key)
        scoped_company_id = getattr(getattr(request, "state", None), "idempotency_company_id", scoped_company_id)

    if scoped_company_id is None:
        return

    async for db in _get_idempotency_db():
        await _idempotency_enforcer.set_result(
            db,
            company_id=int(scoped_company_id),
            key=scoped_key,
            status_code=int(status_code),
            ttl_seconds=ttl_seconds,
        )
        return


# ------------------------------------------------------------------------------
# Provider adapters (registry-aware, default to NoOp)
# ------------------------------------------------------------------------------
async def get_payment_gateway(db=Depends(get_db)):
    from app.integrations.providers.noop import NoOpPaymentGateway
    from app.services.payment_providers import PaymentProviderResolver

    try:
        return await PaymentProviderResolver.resolve(db, domain="payments")
    except Exception as exc:  # pragma: no cover - runtime guard
        if settings.is_production:
            raise HTTPException(status_code=503, detail="payment_provider_not_configured")
        log.warning("Payment gateway resolution failed; using noop", exc_info=exc)
        return NoOpPaymentGateway()


# Alias for DI symmetry
get_payment_service = get_payment_gateway


async def get_otp_service(db=Depends(get_db)):
    from app.services.otp_providers import OtpProviderResolver

    try:
        return await OtpProviderResolver.resolve(db, domain="otp")
    except Exception as exc:  # pragma: no cover - runtime guard
        if settings.is_production:
            raise HTTPException(status_code=503, detail="otp_provider_not_configured")
        log.warning("OTP service resolution failed; using noop", exc_info=exc)
        from app.integrations.providers.noop import NoOpOtpProvider

        return NoOpOtpProvider()


get_otp_provider = get_otp_service


async def get_messaging_provider(db=Depends(get_db)):
    from app.integrations.providers.noop import NoOpMessagingProvider
    from app.services.messaging_providers import MessagingProviderResolver

    try:
        return await MessagingProviderResolver.resolve(db, domain="messaging")
    except Exception as exc:  # pragma: no cover - runtime guard
        if settings.is_production:
            raise HTTPException(status_code=503, detail="messaging_provider_not_configured")
        log.warning("Messaging provider resolution failed; using noop", exc_info=exc)
        return NoOpMessagingProvider()


async def get_media_provider(db=Depends(get_db)):
    from app.integrations.providers.noop.media import NoOpMediaProvider
    from app.services.media_providers import MediaProviderResolver

    try:
        return await MediaProviderResolver.resolve(db, domain="media")
    except Exception as exc:  # pragma: no cover - runtime guard
        if settings.is_production:
            raise HTTPException(status_code=503, detail="media_provider_not_configured")
        log.warning("Media provider resolution failed; using noop", exc_info=exc)
        return NoOpMediaProvider()


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
    "require_active_subscription",
    "get_current_superuser",
    "require_platform_admin",
    "require_roles_strict",
    "require_store_roles",
    "require_store_admin",
    "require_scopes",
    # rate limit
    "rate_limit",
    "auth_rate_limit",
    "api_rate_limit",
    "otp_rate_limit",
    "auth_rate_limit_dep",  # keep aliases
    "api_rate_limit_dep",
    "enforce_rate_limit",
    # pagination
    "Pagination",
    "get_pagination",
    # client info / context
    "get_client_info",
    # idempotency
    "ensure_idempotency",
    "ensure_idempotency_replay",
    "set_idempotency_result",
    # providers
    "get_payment_gateway",
    "get_payment_service",
    "get_otp_service",
    "get_otp_provider",
    "get_messaging_provider",
    "get_media_provider",
]
