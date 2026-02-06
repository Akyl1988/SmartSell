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

import json
import re
import uuid
from collections.abc import Mapping
from typing import Any

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

try:
    # SQLAlchemy is optional at import time
    from sqlalchemy.exc import IntegrityError, OperationalError, SQLAlchemyError  # type: ignore
except Exception:  # pragma: no cover
    IntegrityError = type("IntegrityError", (Exception,), {})  # type: ignore
    SQLAlchemyError = type("SQLAlchemyError", (Exception,), {})  # type: ignore
    OperationalError = type("OperationalError", (Exception,), {})  # type: ignore

# Logging (structlog-aware)
from app.core.logging import bound_context, get_logger

logger = get_logger(__name__)


def _json_safe_errors(errs):
    try:
        return json.loads(json.dumps(errs, default=str))
    except Exception:
        return [{"msg": "Validation error"}]


# -----------------------------------------------------------------------------
# Custom domain exceptions
# -----------------------------------------------------------------------------


class SmartSellException(Exception):
    """Base domain exception."""

    def __init__(
        self,
        message: str,
        code: str | None = None,
        *,
        extra: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        http_status: int | None = None,
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
    headers: dict[str, str] | None = None,
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
    headers: dict[str, str] | None = None,
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
        out: dict[str, Any] = {}
        for k, v in obj.items():
            lk = str(k).lower()
            if any(x in lk for x in _SECRET_KEYS) and "public" not in lk:
                out[k] = _mask_secret_value(v)
            else:
                out[k] = _redact(v)
        return out
    if isinstance(obj, list | tuple):
        tp = type(obj)
        return tp(_redact(i) for i in obj)
    return obj


def _extract_request_id(headers: Mapping[str, str]) -> str:
    for k in ("x-request-id", "x-correlation-id", "x-amzn-trace-id"):
        if k in headers:
            return headers.get(k, "")
    return ""


def _ensure_request_id(request: Request) -> str:
    rid = ""
    try:
        rid = getattr(request.state, "request_id", "")
    except Exception:
        rid = ""
    if not rid:
        rid = _extract_request_id(request.headers)
    if not rid:
        rid = str(uuid.uuid4())
    try:
        request.state.request_id = rid
    except Exception:
        pass
    return rid


def _stringify_detail(detail: Any) -> str:
    if isinstance(detail, str):
        return detail
    try:
        return json.dumps(detail, ensure_ascii=False)
    except Exception:
        return str(detail)


def _json_error_response(
    request: Request,
    status_code: int,
    detail: Any,
    code: str,
    headers: dict[str, str] | None = None,
    errors: Any | None = None,
) -> JSONResponse:
    rid = _ensure_request_id(request)
    if isinstance(detail, dict | list):
        detail_value: Any = detail
    else:
        detail_value = _stringify_detail(detail)
    payload = {
        "detail": detail_value,
        "code": code,
        "request_id": rid,
    }
    if errors is not None:
        payload["errors"] = _json_safe_errors(errors)
    response_headers = dict(headers or {})
    response_headers.setdefault("X-Request-ID", rid)
    return JSONResponse(status_code=status_code, content=payload, headers=response_headers)


# -----------------------------------------------------------------------------
# IntegrityError parsing (Postgres/SQLite common patterns)
# -----------------------------------------------------------------------------

_DUP_RE = re.compile(r"duplicate key|unique constraint|unique violation", re.IGNORECASE)
_FK_RE = re.compile(r"foreign key", re.IGNORECASE)
_NOTNULL_RE = re.compile(r"not null", re.IGNORECASE)
_CHECK_RE = re.compile(r"check constraint|violates check constraint", re.IGNORECASE)


def _parse_integrity_error(exc: IntegrityError) -> tuple[str, str]:
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
    rid = _ensure_request_id(request)
    with bound_context(request_id=rid):
        logger.error(
            "Unhandled exception",
            exc_info=exc,
            path=request.url.path,
            method=request.method,
        )

    return _json_error_response(
        request=request,
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail="internal_error",
        code="INTERNAL_ERROR",
    )


async def smartsell_exception_handler(request: Request, exc: SmartSellException) -> JSONResponse:
    """
    Handler for our domain exceptions. Maps to appropriate HTTP status codes.
    """
    # Поддержка переопределения статуса через исключение
    sc = exc.http_status or status.HTTP_400_BAD_REQUEST
    code = exc.code or "BAD_REQUEST"

    if isinstance(exc, AuthenticationError):
        sc = exc.http_status or status.HTTP_401_UNAUTHORIZED
        code = exc.code or "AUTHENTICATION_ERROR"
        # Если не задано, добавим WWW-Authenticate (полезно для клиентов)
        exc.headers.setdefault("WWW-Authenticate", 'Bearer realm="api"')
    elif isinstance(exc, AuthorizationError | ForbiddenError):
        sc = exc.http_status or status.HTTP_403_FORBIDDEN
        code = exc.code or ("AUTHORIZATION_ERROR" if isinstance(exc, AuthorizationError) else "FORBIDDEN")
    elif isinstance(exc, NotFoundError):
        sc = exc.http_status or status.HTTP_404_NOT_FOUND
        code = exc.code or "NOT_FOUND"
    elif isinstance(exc, ConflictError):
        # Tests expect a 400 for duplicate phone; keep overrideable via http_status
        sc = exc.http_status or status.HTTP_400_BAD_REQUEST
        code = exc.code or "CONFLICT"
    elif isinstance(exc, RateLimitError):
        sc = exc.http_status or status.HTTP_429_TOO_MANY_REQUESTS
        code = exc.code or "RATE_LIMITED"
    elif isinstance(exc, ExternalServiceError):
        sc = exc.http_status or status.HTTP_502_BAD_GATEWAY
        code = exc.code or "UPSTREAM_ERROR"
    elif isinstance(exc, SmartSellValidationError):
        sc = exc.http_status or status.HTTP_422_UNPROCESSABLE_ENTITY
        code = exc.code or "VALIDATION_ERROR"

    rid = _ensure_request_id(request)
    with bound_context(request_id=rid):
        logger.warning(
            "SmartSell exception",
            exception_type=type(exc).__name__,
            message=exc.message,
            code=exc.code,
            path=request.url.path,
            method=request.method,
            extra=_redact(exc.extra),
        )

    return _json_error_response(
        request=request,
        status_code=sc,
        detail=exc.message,
        code=code,
        headers=exc.headers,
    )


async def integrity_error_handler(request: Request, exc: IntegrityError) -> JSONResponse:
    """
    Handler for DB integrity errors (duplicate, FK, not null, check).
    """
    rid = _ensure_request_id(request)
    msg, code = _parse_integrity_error(exc)

    with bound_context(request_id=rid):
        logger.warning(
            "Database integrity error",
            error=str(getattr(exc, "orig", exc)),
            path=request.url.path,
            method=request.method,
            code=code,
        )

    return _json_error_response(
        request=request,
        status_code=status.HTTP_409_CONFLICT,
        detail=msg,
        code=code,
    )


async def validation_exception_handler(request: Request, exc: ValidationError) -> JSONResponse:
    """
    Handler for Pydantic validation errors.
    """
    rid = _ensure_request_id(request)
    errs = _json_safe_errors(exc.errors())

    with bound_context(request_id=rid):
        logger.warning(
            "Validation error",
            errors=_redact(errs),
            path=request.url.path,
            method=request.method,
        )

    return _json_error_response(
        request=request,
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail="One or more fields failed validation",
        code="VALIDATION_ERROR",
        errors=errs,
    )


async def request_validation_exception_handler(request: Request, exc) -> JSONResponse:
    """
    Handler for FastAPI RequestValidationError (body/query/path validation).
    """
    try:
        pass
    except Exception:  # pragma: no cover
        # если тип не доступен — передадим в общий валидатор
        return await validation_exception_handler(request, ValidationError.from_exception_data("RequestValidation", []))  # type: ignore

    # тип-safe проверка
    if hasattr(exc, "errors"):
        errs = _json_safe_errors(exc.errors())
    else:
        errs = [{"msg": "Validation error"}]

    rid = _ensure_request_id(request)
    with bound_context(request_id=rid):
        logger.warning(
            "Request validation error",
            errors=_redact(errs),
            path=request.url.path,
            method=request.method,
        )

    return _json_error_response(
        request=request,
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail="Request validation failed",
        code="REQUEST_VALIDATION_ERROR",
        errors=errs,
    )


async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    """
    Handler for FastAPI HTTP exceptions (raised via http_error/shortcuts).
    """
    rid = _ensure_request_id(request)
    with bound_context(request_id=rid):
        logger.info(
            "HTTP exception",
            status_code=exc.status_code,
            detail=_redact(exc.detail),
            path=request.url.path,
            method=request.method,
        )

    # Переносим заголовки (например, rate-limit/WWW-Authenticate)
    headers = exc.headers or {}
    return _json_error_response(
        request=request,
        status_code=exc.status_code,
        detail=exc.detail,
        code=f"HTTP_{exc.status_code}",
        headers=headers,
    )


async def sqlalchemy_exception_handler(request: Request, exc: SQLAlchemyError) -> JSONResponse:
    """
    Generic SQLAlchemy errors (not integrity).
    """
    rid = _ensure_request_id(request)
    with bound_context(request_id=rid):
        logger.error(
            "SQLAlchemy error",
            exc_info=exc,
            path=request.url.path,
            method=request.method,
        )

    return _json_error_response(
        request=request,
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail="Database operation failed",
        code="DB_ERROR",
    )


async def operational_error_handler(request: Request, exc: OperationalError) -> JSONResponse:
    """
    Operational DB errors (timeouts, connection issues).
    """
    rid = _ensure_request_id(request)
    with bound_context(request_id=rid):
        logger.error(
            "DB operational error",
            exc_info=exc,
            path=request.url.path,
            method=request.method,
        )

    return _json_error_response(
        request=request,
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Database is temporarily unavailable. Please retry later.",
        code="DB_UNAVAILABLE",
    )


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
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(ValidationError, validation_exception_handler)
    try:
        from fastapi.exceptions import RequestValidationError  # type: ignore

        app.add_exception_handler(RequestValidationError, request_validation_exception_handler)  # type: ignore
    except Exception:  # pragma: no cover
        pass

    # SQLAlchemy
    app.add_exception_handler(IntegrityError, integrity_error_handler)  # 409
    app.add_exception_handler(OperationalError, operational_error_handler)  # 503
    app.add_exception_handler(SQLAlchemyError, sqlalchemy_exception_handler)  # 500

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
