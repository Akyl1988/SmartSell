from __future__ import annotations

from fastapi import Response

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
