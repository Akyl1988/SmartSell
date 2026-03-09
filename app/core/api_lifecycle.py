from __future__ import annotations

import re

API_VERSION = "v1"


_LIFECYCLE_RULES: tuple[dict[str, str | bool], ...] = (
    {
        "method": "POST",
        "path_regex": r"^/api/v1/kaspi/feed/uploads/[^/]+/refresh-status$",
        "deprecated": True,
        "sunset": "Tue, 30 Jun 2026 00:00:00 GMT",
    },
)


def get_api_version() -> str:
    return API_VERSION


def _resolve_endpoint_lifecycle(method: str | None, path: str | None) -> tuple[bool, str | None]:
    if not method or not path:
        return False, None

    method_upper = method.upper()
    for rule in _LIFECYCLE_RULES:
        rule_method = str(rule.get("method") or "").upper()
        if rule_method and rule_method != method_upper:
            continue
        path_regex = str(rule.get("path_regex") or "")
        if path_regex and not re.match(path_regex, path):
            continue
        return bool(rule.get("deprecated")), (str(rule.get("sunset")) if rule.get("sunset") else None)
    return False, None


def get_deprecation_headers(
    *,
    request_method: str | None = None,
    request_path: str | None = None,
    deprecated: bool = False,
    sunset: str | None = None,
) -> dict[str, str]:
    endpoint_deprecated, endpoint_sunset = _resolve_endpoint_lifecycle(request_method, request_path)
    effective_deprecated = deprecated or endpoint_deprecated
    effective_sunset = sunset or endpoint_sunset

    headers = {
        "X-SmartSell-API-Version": get_api_version(),
    }
    if effective_deprecated:
        headers["Deprecation"] = "true"
    if effective_sunset:
        headers["Sunset"] = effective_sunset
    return headers


__all__ = ["API_VERSION", "get_api_version", "get_deprecation_headers"]
