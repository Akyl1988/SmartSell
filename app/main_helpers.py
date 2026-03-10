from __future__ import annotations

import os
from typing import Any

from fastapi import FastAPI

_SECRET_KEYS = ("SECRET", "PASSWORD", "TOKEN", "KEY", "PASS", "PRIVATE", "CREDENTIAL", "AUTH")
_REDACT_KEYS_EXACT = {
    "DATABASE_URL",
    "DB_URL",
    "REDIS_URL",
    "SQLALCHEMY_DATABASE_URI",
}


def env_truthy(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on", "enable", "enabled")


def env_int(name: str, default: int = 0) -> int:
    try:
        return int(os.getenv(name, str(default)) or default)
    except Exception:
        return default


def is_postgres_url(url: str | None) -> bool:
    if not url:
        return False
    value = (url or "").lower()
    return value.startswith("postgres://") or value.startswith("postgresql://")


def env_last_deploy_time() -> str:
    for key in ("LAST_DEPLOY_AT", "LAST_DEPLOY_TIME", "DEPLOYED_AT", "DEPLOY_TIME"):
        value = os.getenv(key)
        if value:
            return value
    return ""


def parse_trusted_hosts() -> list[str] | None:
    raw = os.getenv("TRUSTED_HOSTS", "")
    if not raw:
        return None
    return [host.strip() for host in raw.split(",") if host.strip()]


def redact(value: Any) -> Any:
    try:
        if value is None:
            return None
        string_value = str(value)
        if not string_value:
            return string_value
        if len(string_value) <= 6:
            return "***"
        return string_value[:2] + "…" + string_value[-2:]
    except Exception:
        return "***"


def redact_dict(data: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in data.items():
        key_upper = str(key).upper()
        if key_upper in _REDACT_KEYS_EXACT or any(part in key_upper for part in _SECRET_KEYS):
            out[key] = redact(value)
        else:
            out[key] = value
    return out


def has_path_prefix(app: FastAPI, prefix: str) -> bool:
    """Проверяет, что в приложении уже есть хотя бы один маршрут на указанный префикс."""
    try:
        for route in app.router.routes:
            route_path = getattr(route, "path", None) or getattr(route, "path_format", None)
            if isinstance(route_path, str) and route_path.startswith(prefix):
                return True
    except Exception:
        pass
    return False
