from __future__ import annotations

import re


def mask_phone(value: str) -> str:
    raw = value or ""
    digits = re.sub(r"\D", "", raw)
    if not digits:
        return ""
    if len(digits) <= 4:
        return "*" * len(digits)
    prefix_len = 2 if len(digits) >= 6 else 1
    suffix_len = 4 if len(digits) >= 6 else 2
    prefix = digits[:prefix_len]
    suffix = digits[-suffix_len:]
    masked = prefix + ("*" * max(1, len(digits) - prefix_len - suffix_len)) + suffix
    if raw.strip().startswith("+"):
        return f"+{masked}"
    return masked


def mask_email(value: str) -> str:
    v = (value or "").strip()
    if not v or "@" not in v:
        return "***"
    local, domain = v.split("@", 1)
    if not local:
        return f"***@{domain}"
    head = local[0]
    return f"{head}***@{domain}"


__all__ = ["mask_phone", "mask_email"]
