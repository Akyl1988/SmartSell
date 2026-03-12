from __future__ import annotations

import os
import time
import uuid
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

from app.core.api_lifecycle import get_deprecation_headers


def apply_security_and_lifecycle_headers(
    response: Response,
    *,
    request_method: str,
    request_path: str,
    csp_enabled: bool,
    csp_value: str,
    force_https: bool,
) -> None:
    for header_name, header_value in get_deprecation_headers(
        request_method=request_method,
        request_path=request_path,
    ).items():
        response.headers.setdefault(header_name, header_value)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("X-XSS-Protection", "0")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    if force_https:
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains; preload")
    if csp_enabled:
        response.headers.setdefault("Content-Security-Policy", csp_value)
    response.headers.setdefault("Server", "SmartSell")


def register_response_time_middleware(app: FastAPI) -> None:
    @app.middleware("http")
    async def response_time_middleware(  # type: ignore[return-value]
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        start = time.perf_counter()
        response = await call_next(request)
        try:
            ms = int((time.perf_counter() - start) * 1000)
            response.headers.setdefault("X-Response-Time-ms", str(ms))
        except Exception:
            pass
        return response


def register_content_length_guard(app: FastAPI, *, max_body: int) -> None:
    @app.middleware("http")
    async def content_length_guard(  # type: ignore[return-value]
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        try:
            if max_body > 0:
                cl = request.headers.get("content-length")
                if cl and cl.isdigit() and int(cl) > max_body:
                    rid = getattr(request.state, "request_id", None) or request.headers.get("X-Request-ID")
                    if not rid:
                        rid = str(uuid.uuid4())
                        try:
                            request.state.request_id = rid
                        except Exception:
                            pass
                    return JSONResponse(
                        status_code=413,
                        content={
                            "detail": "request_entity_too_large",
                            "code": "REQUEST_ENTITY_TOO_LARGE",
                            "request_id": rid,
                        },
                        headers={"X-Request-ID": rid},
                    )
        except Exception:
            pass
        return await call_next(request)


def register_security_headers_middleware(
    app: FastAPI,
    *,
    csp_enabled: bool,
    csp_value: str,
    env_truthy_fn: Callable[[str | None, bool], bool],
) -> None:
    @app.middleware("http")
    async def security_headers_mw(  # type: ignore[return-value]
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        response = await call_next(request)
        apply_security_and_lifecycle_headers(
            response,
            request_method=request.method,
            request_path=request.url.path,
            csp_enabled=csp_enabled,
            csp_value=csp_value,
            force_https=env_truthy_fn(os.getenv("FORCE_HTTPS", "0"), False),
        )
        return response


def register_request_id_middleware(
    app: FastAPI,
    *,
    request_id_var: ContextVar[str],
    hostname: str,
) -> None:
    @app.middleware("http")
    async def request_id_middleware(  # type: ignore[return-value]
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        req_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        try:
            request.state.request_id = req_id
        except Exception:
            pass
        token = request_id_var.set(req_id)
        try:
            response = await call_next(request)
            response.headers["X-Request-ID"] = req_id
            response.headers.setdefault("X-Process-Id", str(os.getpid()))
            response.headers.setdefault("X-Hostname", hostname)
            return response
        finally:
            try:
                request_id_var.reset(token)
            except Exception:
                pass


def register_profiling_middleware(
    app: FastAPI,
    *,
    profiled_call: Callable[[Request, Callable[[Request], Awaitable[Response]]], Awaitable[Response]],
) -> None:
    @app.middleware("http")
    async def profiling_middleware(  # type: ignore[return-value]
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        return await profiled_call(request, call_next)


def register_request_completion_logging_middleware(
    app: FastAPI,
    *,
    request_observability_logger: Any,
) -> None:
    @app.middleware("http")
    async def request_completion_logging_middleware(  # type: ignore[return-value]
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        started_at = time.perf_counter()
        status_code = 500
        try:
            response = await call_next(request)
            status_code = int(getattr(response, "status_code", 200) or 200)
            return response
        except Exception:
            raise
        finally:
            duration_ms = int((time.perf_counter() - started_at) * 1000)
            request_id = (
                getattr(request.state, "request_id", None)
                or request.headers.get("X-Request-ID")
                or request.headers.get("X-Correlation-ID")
            )
            company_id = (
                getattr(request.state, "company_id", None)
                or getattr(request.state, "tenant_id", None)
                or request.headers.get("X-Company-ID")
            )
            request_observability_logger.info(
                "request_completed",
                extra={
                    "request_id": request_id,
                    "company_id": company_id,
                    "method": request.method,
                    "path": request.url.path,
                    "status_code": status_code,
                    "duration_ms": duration_ms,
                },
            )


def register_external_diag_timing_middleware(app: FastAPI) -> None:
    @app.middleware("http")
    async def external_diag_timing_mw(  # type: ignore[return-value]
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if request.url.path != "/api/v1/_debug/external":
            return await call_next(request)
        t0 = time.perf_counter()
        t_pre_end = time.perf_counter()
        response = await call_next(request)
        t_call_end = time.perf_counter()
        t_post_end = time.perf_counter()
        response.headers["x-mw-pre-ms"] = str(int((t_pre_end - t0) * 1000))
        response.headers["x-mw-callnext-ms"] = str(int((t_call_end - t_pre_end) * 1000))
        response.headers["x-mw-post-ms"] = str(int((t_post_end - t_call_end) * 1000))
        response.headers["x-total-ms"] = str(int((t_post_end - t0) * 1000))
        return response
