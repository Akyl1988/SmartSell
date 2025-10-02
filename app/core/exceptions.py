# app/core/exceptions.py
from __future__ import annotations

"""
Unified exceptions & handlers for SmartSell3 (enterprise-grade).

- Custom domain exceptions (AuthenticationError, AuthorizationError, ForbiddenError, etc.)
- HTTP shortcuts (bad_request, unauthorized, ...)
- Global FastAPI handlers with structured logging via app.core.logging
- RFC 7807-style JSON body (problem+json-compatible fields)
- IntegrityError parsing (duplicate/foreign key/not null/check) for PG/SQLite
- ValidationError, RequestValidationError, HTTPException, SQLAlchemyError, OperationalError handling
- Optional rate-limit headers passthrough (Retry-After, X-RateLimit-*)
- Request context enrichment (request_id, method, path)
"""

import re
from typing import Any, Dict, Optional, Tuple, Mapping

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import ValidationError

try:
    # SQLAlchemy is optional at import time
    from sqlalchemy.exc import IntegrityError, SQLAlchemyError, OperationalError  # type: ignore
except Exception:  # pragma: no cover
    IntegrityError = type("IntegrityError", (Exception,), {})  # type: ignore
    SQLAlchemyError = type("SQLAlchemyError", (Exception,), {})  # type: ignore
    OperationalError = type("OperationalError", (Exception,), {})  # type: ignore

# Logging (structlog-aware)
from app.core.logging import get_logger, bind_context

logger = get_logger(__name__)

# -----------------------------------------------------------------------------
# Custom domain exceptions
# -----------------------------------------------------------------------------

class SmartSellException(Exception):
    """Base domain exception."""
    def __init__(
        self,
        message: str,
        code: Optional[str] = None,
        *,
        extra: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        http_status: Optional[int] = None,
    ):
        self.message = message
        self.code = code
        self.extra = extra or {}
        self.headers = headers or {}
        self.http_status = http_status  # позволяет насильно указать статус
        super().__init__(self.message)

class AuthenticationError(SmartSellException):
    """Authentication related errors."""

class AuthorizationError(SmartSellException):
    """Authorization related errors."""

class ForbiddenError(AuthorizationError):
    """Explicit '403 Forbidden' (kept for backward-compat)."""

class SmartSellValidationError(SmartSellException):
    """Validation related errors."""

class NotFoundError(SmartSellException):
    """Resource not found errors."""

class ConflictError(SmartSellException):
    """Resource conflict errors."""

class RateLimitError(SmartSellException):
    """Rate limiting errors."""

class ExternalServiceError(SmartSellException):
    """External service errors."""

# -----------------------------------------------------------------------------
# HTTP shortcuts (factory style, single point of truth)
# -----------------------------------------------------------------------------

def http_error(
    status_code: int,
    detail: str,
    headers: Optional[Dict[str, str]] = None,
) -> HTTPException:
    """Single factory for HTTP errors (consistent style project-wide)."""
    return HTTPException(status_code=status_code, detail=detail, headers=headers)

def bad_request(detail: str) -> HTTPException:
    return http_error(status.HTTP_400_BAD_REQUEST, detail)

def unauthorized(detail: str = "Unauthorized", *, www_authenticate: str | None = 'Bearer realm="api"') -> HTTPException:
    headers = {"WWW-Authenticate": www_authenticate} if www_authenticate else None
    return http_error(status.HTTP_401_UNAUTHORIZED, detail, headers=headers)

def forbidden(detail: str = "Forbidden") -> HTTPException:
    return http_error(status.HTTP_403_FORBIDDEN, detail)

def not_found(detail: str = "Not found") -> HTTPException:
    return http_error(status.HTTP_404_NOT_FOUND, detail)

def conflict(detail: str = "Conflict") -> HTTPException:
    return http_error(status.HTTP_409_CONFLICT, detail)

def too_many_requests(
    detail: str = "Too Many Requests",
    headers: Optional[Dict[str, str]] = None,
) -> HTTPException:
    return http_error(status.HTTP_429_TOO_MANY_REQUESTS, detail, headers=headers)

def server_error(detail: str = "Internal Server Error") -> HTTPException:
    return http_error(status.HTTP_500_INTERNAL_SERVER_ERROR, detail)

# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------

_SECRET_KEYS = ("secret", "password", "token", "api_key", "api_secret", "access_key", "dsn", "key")

def _mask_secret_value(v: Any) -> Any:
    try:
        s = str(v)
    except Exception:
        return "***"
    if len(s) <= 6:
        return "***"
    return s[:3] + "***" + s[-3:]

def _redact(obj: Any) -> Any:
    if isinstance(obj, Mapping):
        out: Dict[str, Any] = {}
        for k, v in obj.items():
            lk = str(k).lower()
            if any(x in lk for x in _SECRET_KEYS) and "public" not in lk:
                out[k] = _mask_secret_value(v)
            else:
                out[k] = _redact(v)
        return out
    if isinstance(obj, (list, tuple)):
        tp = type(obj)
        return tp(_redact(i) for i in obj)
    return obj

def _problem_json(
    title: str,
    detail: str,
    status_code: int,
    code: Optional[str] = None,
    instance: Optional[str] = None,
    extras: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    RFC 7807 inspired body (application/problem+json compatible).
    Kept keys also compatible with former response shape.
    """
    body: Dict[str, Any] = {
        "type": f"https://httpstatuses.com/{status_code}",
        "title": title,
        "status": status_code,
        "detail": detail,
        "error": title,          # backward-compat field
        "code": code,
    }
    if instance:
        body["instance"] = instance
    if extras:
        body["extra"] = _redact(extras)
    # remove None
    return {k: v for k, v in body.items() if v is not None}

def _extract_request_id(headers: Mapping[str, str]) -> str:
    for k in ("x-request-id", "x-correlation-id", "x-amzn-trace-id"):
        if k in headers:
            return headers.get(k, "")
    return ""

def _json_problem_response(
    status_code: int,
    content: Dict[str, Any],
    headers: Optional[Dict[str, str]] = None,
) -> JSONResponse:
    # Явно выставляем media_type для совместимости с RFC7807
    return JSONResponse(status_code=status_code, content=content, headers=headers or {}, media_type="application/problem+json")

# -----------------------------------------------------------------------------
# IntegrityError parsing (Postgres/SQLite common patterns)
# -----------------------------------------------------------------------------

_DUP_RE = re.compile(r"duplicate key|unique constraint|unique violation", re.IGNORECASE)
_FK_RE = re.compile(r"foreign key", re.IGNORECASE)
_NOTNULL_RE = re.compile(r"not null", re.IGNORECASE)
_CHECK_RE = re.compile(r"check constraint|violates check constraint", re.IGNORECASE)

def _parse_integrity_error(exc: IntegrityError) -> Tuple[str, str]:
    """
    Returns (message, code) for user-friendly error mapping.
    """
    text = str(getattr(exc, "orig", exc))
    if _DUP_RE.search(text):
        return ("A record with this value already exists", "DUPLICATE_VALUE")
    if _FK_RE.search(text):
        return ("Referenced record does not exist", "FOREIGN_KEY_ERROR")
    if _NOTNULL_RE.search(text):
        return ("Required field is missing", "REQUIRED_FIELD")
    if _CHECK_RE.search(text):
        return ("Invalid value provided", "INVALID_VALUE")
    return ("A database constraint was violated", "INTEGRITY_ERROR")

# -----------------------------------------------------------------------------
# Exception Handlers (FastAPI)
# -----------------------------------------------------------------------------

async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """
    Fallback handler for uncaught exceptions.
    """
    rid = _extract_request_id(request.headers)
    with bind_context(request_id=rid):
        logger.error(
            "Unhandled exception",
            exc_info=exc,
            path=request.url.path,
            method=request.method,
        )

    body = _problem_json(
        title="Internal server error",
        detail="An unexpected error occurred. Please try again later.",
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        code="INTERNAL_ERROR",
        instance=str(request.url),
    )
    return _json_problem_response(status.HTTP_500_INTERNAL_SERVER_ERROR, body)

async def smartsell_exception_handler(request: Request, exc: SmartSellException) -> JSONResponse:
    """
    Handler for our domain exceptions. Maps to appropriate HTTP status codes.
    """
    # Поддержка переопределения статуса через исключение
    sc = exc.http_status or status.HTTP_400_BAD_REQUEST
    title = "Bad request"

    if isinstance(exc, AuthenticationError):
        sc = exc.http_status or status.HTTP_401_UNAUTHORIZED
        title = "Authentication error"
        # Если не задано, добавим WWW-Authenticate (полезно для клиентов)
        exc.headers.setdefault("WWW-Authenticate", 'Bearer realm="api"')
    elif isinstance(exc, (AuthorizationError, ForbiddenError)):
        sc = exc.http_status or status.HTTP_403_FORBIDDEN
        title = "Authorization error" if isinstance(exc, AuthorizationError) and not isinstance(exc, ForbiddenError) else "Forbidden"
    elif isinstance(exc, NotFoundError):
        sc = exc.http_status or status.HTTP_404_NOT_FOUND
        title = "Resource not found"
    elif isinstance(exc, ConflictError):
        sc = exc.http_status or status.HTTP_409_CONFLICT
        title = "Conflict"
    elif isinstance(exc, RateLimitError):
        sc = exc.http_status or status.HTTP_429_TOO_MANY_REQUESTS
        title = "Too Many Requests"
    elif isinstance(exc, ExternalServiceError):
        sc = exc.http_status or status.HTTP_502_BAD_GATEWAY
        title = "Upstream service error"
    elif isinstance(exc, SmartSellValidationError):
        sc = exc.http_status or status.HTTP_422_UNPROCESSABLE_ENTITY
        title = "Validation error"

    rid = _extract_request_id(request.headers)
    with bind_context(request_id=rid):
        logger.warning(
            "SmartSell exception",
            exception_type=type(exc).__name__,
            message=exc.message,
            code=exc.code,
            path=request.url.path,
            method=request.method,
            extra=_redact(exc.extra),
        )

    body = _problem_json(
        title=title,
        detail=exc.message,
        status_code=sc,
        code=exc.code,
        instance=str(request.url),
        extras=exc.extra,
    )
    return _json_problem_response(sc, body, headers=exc.headers)

async def integrity_error_handler(request: Request, exc: IntegrityError) -> JSONResponse:
    """
    Handler for DB integrity errors (duplicate, FK, not null, check).
    """
    rid = _extract_request_id(request.headers)
    msg, code = _parse_integrity_error(exc)

    with bind_context(request_id=rid):
        logger.warning(
            "Database integrity error",
            error=str(getattr(exc, "orig", exc)),
            path=request.url.path,
            method=request.method,
            code=code,
        )

    body = _problem_json(
        title="Integrity error",
        detail=msg,
        status_code=status.HTTP_409_CONFLICT,
        code=code,
        instance=str(request.url),
    )
    return _json_problem_response(status.HTTP_409_CONFLICT, body)

async def validation_exception_handler(request: Request, exc: ValidationError) -> JSONResponse:
    """
    Handler for Pydantic validation errors.
    """
    rid = _extract_request_id(request.headers)
    errs = exc.errors()

    with bind_context(request_id=rid):
        logger.warning(
            "Validation error",
            errors=_redact(errs),
            path=request.url.path,
            method=request.method,
        )

    body = _problem_json(
        title="Validation error",
        detail="One or more fields failed validation",
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        code="VALIDATION_ERROR",
        instance=str(request.url),
        extras={"errors": errs},
    )
    return _json_problem_response(status.HTTP_422_UNPROCESSABLE_ENTITY, body)

async def request_validation_exception_handler(request: Request, exc) -> JSONResponse:
    """
    Handler for FastAPI RequestValidationError (body/query/path validation).
    """
    try:
        from fastapi.exceptions import RequestValidationError  # local import to avoid hard dependency
    except Exception:  # pragma: no cover
        # если тип не доступен — передадим в общий валидатор
        return await validation_exception_handler(request, ValidationError.from_exception_data("RequestValidation", []))  # type: ignore

    # тип-safe проверка
    if hasattr(exc, "errors"):
        errs = exc.errors()
    else:
        errs = [{"msg": "Validation error"}]

    rid = _extract_request_id(request.headers)
    with bind_context(request_id=rid):
        logger.warning(
            "Request validation error",
            errors=_redact(errs),
            path=request.url.path,
            method=request.method,
        )

    body = _problem_json(
        title="Validation error",
        detail="Request validation failed",
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        code="REQUEST_VALIDATION_ERROR",
        instance=str(request.url),
        extras={"errors": errs},
    )
    return _json_problem_response(status.HTTP_422_UNPROCESSABLE_ENTITY, body)

async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    """
    Handler for FastAPI HTTP exceptions (raised via http_error/shortcuts).
    """
    rid = _extract_request_id(request.headers)
    with bind_context(request_id=rid):
        logger.info(
            "HTTP exception",
            status_code=exc.status_code,
            detail=_redact(exc.detail),
            path=request.url.path,
            method=request.method,
        )

    # Переносим заголовки (например, rate-limit/WWW-Authenticate)
    headers = exc.headers or {}
    body = _problem_json(
        title=f"HTTP {exc.status_code}",
        detail=str(exc.detail),
        status_code=exc.status_code,
        code=f"HTTP_{exc.status_code}",
        instance=str(request.url),
    )
    return _json_problem_response(exc.status_code, body, headers=headers)

async def sqlalchemy_exception_handler(request: Request, exc: SQLAlchemyError) -> JSONResponse:
    """
    Generic SQLAlchemy errors (not integrity).
    """
    rid = _extract_request_id(request.headers)
    with bind_context(request_id=rid):
        logger.error(
            "SQLAlchemy error",
            exc_info=exc,
            path=request.url.path,
            method=request.method,
        )

    body = _problem_json(
        title="Database error",
        detail="Database operation failed",
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        code="DB_ERROR",
        instance=str(request.url),
    )
    return _json_problem_response(status.HTTP_500_INTERNAL_SERVER_ERROR, body)

async def operational_error_handler(request: Request, exc: OperationalError) -> JSONResponse:
    """
    Operational DB errors (timeouts, connection issues).
    """
    rid = _extract_request_id(request.headers)
    with bind_context(request_id=rid):
        logger.error(
            "DB operational error",
            exc_info=exc,
            path=request.url.path,
            method=request.method,
        )

    body = _problem_json(
        title="Database unavailable",
        detail="Database is temporarily unavailable. Please retry later.",
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        code="DB_UNAVAILABLE",
        instance=str(request.url),
    )
    return _json_problem_response(status.HTTP_503_SERVICE_UNAVAILABLE, body)

# -----------------------------------------------------------------------------
# Registration
# -----------------------------------------------------------------------------

def register_exception_handlers(app: FastAPI) -> None:
    """
    Attach all exception handlers to FastAPI app.
    """
    # Domain
    app.add_exception_handler(SmartSellException, smartsell_exception_handler)

    # HTTP / Pydantic / FastAPI request-level validation
    app.add_exception_handler(HTTPException, http_exception_handler)
    app.add_exception_handler(ValidationError, validation_exception_handler)
    try:
        from fastapi.exceptions import RequestValidationError  # type: ignore
        app.add_exception_handler(RequestValidationError, request_validation_exception_handler)  # type: ignore
    except Exception:  # pragma: no cover
        pass

    # SQLAlchemy
    app.add_exception_handler(IntegrityError, integrity_error_handler)          # 409
    app.add_exception_handler(OperationalError, operational_error_handler)      # 503
    app.add_exception_handler(SQLAlchemyError, sqlalchemy_exception_handler)    # 500

    # Fallback
    app.add_exception_handler(Exception, global_exception_handler)

# -----------------------------------------------------------------------------
# Public exports
# -----------------------------------------------------------------------------

__all__ = [
    # Exceptions
    "SmartSellException",
    "AuthenticationError",
    "AuthorizationError",
    "ForbiddenError",
    "SmartSellValidationError",
    "NotFoundError",
    "ConflictError",
    "RateLimitError",
    "ExternalServiceError",
    # HTTP shortcuts
    "http_error",
    "bad_request",
    "unauthorized",
    "forbidden",
    "not_found",
    "conflict",
    "too_many_requests",
    "server_error",
    # Handlers registration
    "register_exception_handlers",
]
