from __future__ import annotations

import re
from pathlib import Path


def _looks_like_secret_value(value: str) -> bool:
    lowered = value.strip().strip('"').strip("'").lower()
    placeholders = {
        "",
        "changeme",
        "example",
        "disabled",
        "not-set",
        "notset",
        "your_value_here",
        "replace_me",
        "xxxxx",
        "xxxx",
        "none",
    }
    if lowered in placeholders:
        return False
    return True


def test_env_example_exists_and_safe():
    root = Path(__file__).resolve().parents[1]
    env_path = root / ".env.example"
    assert env_path.exists(), ".env.example is required"

    content = env_path.read_text(encoding="utf-8")
    lines = [line for line in content.splitlines() if line.strip() and not line.strip().startswith("#")]

    # No DSNs with embedded passwords.
    assert not re.search(r"postgres(?:ql)?://[^\s:@]+:[^\s@]+@", content, re.IGNORECASE)

    # No obvious JWTs
    assert not re.search(r"eyJ[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+", content)

    for line in lines:
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not value:
            continue
        key_upper = key.upper()
        sensitive_tokens = ["SECRET", "PASSWORD", "API_KEY", "API_SECRET", "DSN"]
        token_keys = "TOKEN" in key_upper
        token_is_expiry = any(tag in key_upper for tag in ["TOKEN_EXPIRE", "TOKEN_TTL", "TOKEN_WINDOW", "TOKEN_RATE"])
        password_is_policy = "PASSWORD" in key_upper and any(tag in key_upper for tag in ["MIN", "MAX", "LENGTH"])

        if (any(token in key_upper for token in sensitive_tokens) and not password_is_policy) or (
            token_keys and not token_is_expiry
        ):
            assert not _looks_like_secret_value(value), f"{key} looks like a real secret"
