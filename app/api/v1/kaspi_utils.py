from __future__ import annotations

import os
from typing import Any

import httpx
from fastapi import HTTPException, status

from app.core.config import settings


def is_dev_environment() -> bool:
    env_name = str(getattr(settings, "ENVIRONMENT", "") or "").lower()
    debug_flag = bool(getattr(settings, "DEBUG", False))
    if debug_flag:
        return True
    if env_name in {"local", "development", "dev", "test", "testing", "pytest"}:
        return True
    return bool(os.environ.get("PYTEST_CURRENT_TEST"))


def ci_diag_enabled() -> bool:
    return os.environ.get("CI_DIAG", "").strip() == "1"


def normalize_name(name: str) -> str:
    return name.strip().lower()


def mask_secret(value: str | None, *, head: int = 6, tail: int = 4) -> str | None:
    if not value:
        return None
    if len(value) <= head + tail:
        return value
    return f"{value[:head]}...{value[-tail:]}"


def build_kaspi_orders_params(
    *,
    ge_ms: int,
    le_ms: int,
    state: str | None = None,
    page_number: int = 0,
    page_size: int = 1,
) -> dict[str, int | str]:
    params: dict[str, int | str] = {
        "page[number]": int(page_number),
        "page[size]": int(page_size),
        "filter[orders][creationDate][$ge]": int(ge_ms),
        "filter[orders][creationDate][$le]": int(le_ms),
    }
    effective_state = (state or "NEW").strip()
    if effective_state:
        params["filter[orders][state]"] = effective_state
    return params


def kaspi_stub_enabled() -> bool:
    raw = os.environ.get("SMARTSELL_KASPI_STUB", "")
    if not raw:
        return False
    return raw.strip().lower() in {"1", "true", "yes", "on", "enable", "enabled"}


def probe_response_snippet(value: str | None, limit: int = 200) -> str:
    if not value:
        return ""
    text_value = value.strip()
    if len(text_value) <= limit:
        return text_value
    return f"{text_value[:limit]}..."


def extract_httpx_root_cause(exc: Exception) -> tuple[str | None, str | None]:
    cause = getattr(exc, "__cause__", None) or getattr(exc, "__context__", None)
    if not cause:
        return None, None
    return type(cause).__name__, str(cause)


def classify_httpx_error(exc: Exception, root_cause_type: str | None, root_cause_message: str | None) -> str:
    if isinstance(exc, httpx.TimeoutException):
        return "timeout"
    if isinstance(exc, httpx.ConnectTimeout):
        return "connect_timeout"
    if isinstance(exc, httpx.ReadTimeout):
        return "read_timeout"
    if isinstance(exc, httpx.WriteTimeout):
        return "write_timeout"
    if isinstance(exc, httpx.ConnectError):
        return "connect_error"
    cause_type = (root_cause_type or "").lower()
    cause_msg = (root_cause_message or "").lower()
    if "ssl" in cause_type or "tls" in cause_type or "ssl" in cause_msg or "tls" in cause_msg:
        return "tls_error"
    if "dns" in cause_type or "gaierror" in cause_type or "name or service" in cause_msg:
        return "dns_error"
    return "request_error"


def extract_import_code(payload: dict[str, Any]) -> str | None:
    return payload.get("importCode") or payload.get("import_code") or payload.get("code") or payload.get("id")


def normalize_goods_payload(payload: list[dict[str, Any]] | dict[str, Any]) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        data = payload.get("data")
        if isinstance(data, list):
            return data
        return [payload]
    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="payload_required")


def extract_schema_required_fields(schema: dict[str, Any]) -> list[str]:
    required = schema.get("required")
    if isinstance(required, list):
        return [str(item) for item in required if item]
    fields = schema.get("fields")
    if isinstance(fields, list):
        required_fields: list[str] = []
        for entry in fields:
            if not isinstance(entry, dict):
                continue
            if entry.get("required") is True and entry.get("name"):
                required_fields.append(str(entry["name"]))
        return required_fields
    return []


def extract_schema_types(schema: dict[str, Any]) -> dict[str, str]:
    properties = schema.get("properties")
    if isinstance(properties, dict):
        types: dict[str, str] = {}
        for key, value in properties.items():
            if not isinstance(value, dict):
                continue
            field_type = value.get("type")
            if field_type:
                types[str(key)] = str(field_type)
        return types
    fields = schema.get("fields")
    if isinstance(fields, list):
        types = {}
        for entry in fields:
            if not isinstance(entry, dict):
                continue
            name = entry.get("name")
            field_type = entry.get("type")
            if name and field_type:
                types[str(name)] = str(field_type)
        return types
    return {}
