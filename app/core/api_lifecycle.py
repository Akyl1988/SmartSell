from __future__ import annotations

API_VERSION = "v1"


def get_api_version() -> str:
    return API_VERSION


def get_deprecation_headers(*, deprecated: bool = False, sunset: str | None = None) -> dict[str, str]:
    headers = {
        "X-SmartSell-API-Version": get_api_version(),
    }
    if deprecated:
        headers["Deprecation"] = "true"
    if sunset:
        headers["Sunset"] = sunset
    return headers


__all__ = ["API_VERSION", "get_api_version", "get_deprecation_headers"]
