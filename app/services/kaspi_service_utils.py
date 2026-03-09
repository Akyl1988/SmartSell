from __future__ import annotations

import json
import os
from collections.abc import Mapping
from datetime import datetime
from typing import Any, Optional
from urllib.parse import urlparse, urlunparse


def _diag_enabled() -> bool:
    """Check if CI diagnostic logging is enabled."""
    return os.environ.get("CI_DIAG", "").strip() == "1"


def _utcnow() -> datetime:
    return datetime.utcnow()


def _as_str(v: Any) -> str:
    return "" if v is None else str(v)


DEFAULT_KASPI_ORDER_STATES = (
    "NEW",
    "SIGN_REQUIRED",
    "PICKUP",
    "DELIVERY",
    "KASPI_DELIVERY",
    "ARCHIVE",
)


def _parse_kaspi_states(raw: Any) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        parts = [part.strip() for part in raw.replace(";", ",").split(",")]
        return [part.upper() for part in parts if part]
    if isinstance(raw, list | tuple | set):
        states: list[str] = []
        for item in raw:
            if item is None:
                continue
            value = str(item).strip()
            if not value:
                continue
            states.append(value.upper())
        return states
    return []


def _normalize_address(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, dict | list):
        try:
            return json.dumps(value, separators=(",", ":"), sort_keys=True)
        except (TypeError, ValueError):
            return str(value)
    return str(value)


def _extract_kaspi_order_attrs(payload: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "plannedDeliveryDate",
        "reservationDate",
        "preOrder",
        "deliveryMode",
        "deliveryAddress",
        "deliveryCost",
        "deliveryCostForSeller",
        "isKaspiDelivery",
    )
    return {key: payload[key] for key in keys if key in payload}


def _epoch_ms_to_utc_iso(value: Any) -> str | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    try:
        ms = int(value)
    except (TypeError, ValueError):
        return None
    if ms <= 0:
        return None
    dt = datetime.utcfromtimestamp(ms / 1000)
    return f"{dt.isoformat(timespec='seconds')}Z"


def _merge_kaspi_internal_notes(existing: Any, kaspi_attrs: dict[str, Any]) -> dict[str, Any]:
    base: dict[str, Any]
    if existing is None:
        base = {}
    elif isinstance(existing, dict):
        base = dict(existing)
    elif isinstance(existing, str):
        try:
            parsed = json.loads(existing) if existing.strip() else {}
        except json.JSONDecodeError:
            parsed = {"text": existing}
        base = parsed if isinstance(parsed, dict) else {"text": existing}
    else:
        base = {}

    kaspi = base.get("kaspi")
    if not isinstance(kaspi, dict):
        kaspi = {}
    for key, value in kaspi_attrs.items():
        kaspi[key] = value
    base["kaspi"] = kaspi
    return base


def _first_present(data: Mapping[str, Any], *keys: str) -> Any | None:
    for key in keys:
        if key in data:
            return data[key]
    return None


def _xml_escape(s: str) -> str:
    return (
        s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;").replace("'", "&apos;")
    )


def _cdata(s: Optional[str]) -> str:
    if not s:
        return "<![CDATA[]]>"
    safe = s.replace("]]>", "]]&gt;")
    return f"<![CDATA[{safe}]]>"


def _mask_token(value: str | None, *, head: int = 6, tail: int = 4) -> str:
    if not value:
        return ""
    if len(value) <= head + tail:
        return value
    return f"{value[:head]}...{value[-tail:]}"


def _response_snippet(value: str | None, limit: int = 800) -> str:
    if not value:
        return ""
    text_value = value.strip()
    if len(text_value) <= limit:
        return text_value
    return f"{text_value[:limit]}..."


def _extract_kaspi_error_title(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    errors = payload.get("errors")
    if isinstance(errors, list) and errors:
        first = errors[0]
        if isinstance(first, dict):
            title = first.get("title") or first.get("detail")
            if title:
                return str(title)
    if "error" in payload and payload.get("error"):
        return str(payload.get("error"))
    if "detail" in payload and payload.get("detail"):
        return str(payload.get("detail"))
    return None


def _normalize_kaspi_base_url(value: str) -> str:
    if not value:
        return value
    parsed = urlparse(value)
    if not parsed.netloc:
        return value
    path = (parsed.path or "").rstrip("/")
    if parsed.netloc.endswith("kaspi.kz") and path in {"", "/"}:
        return urlunparse(parsed._replace(path="/shop/api"))
    return value
