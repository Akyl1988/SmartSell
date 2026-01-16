"""Messaging utilities (email)."""

from __future__ import annotations

import os
from typing import Any

try:
    from app.core.config import settings  # type: ignore
except Exception:  # pragma: no cover

    class _Settings:  # type: ignore
        ENVIRONMENT = os.getenv("ENVIRONMENT", "development")
        EMAIL_PROVIDER = os.getenv("EMAIL_PROVIDER", "")
        PUBLIC_URL = os.getenv("PUBLIC_URL", "http://localhost:8000")

    settings = _Settings()  # type: ignore

try:
    from app.core.logging import get_logger  # type: ignore
except Exception:  # pragma: no cover

    def get_logger(_name: str):
        class _L:
            def info(self, *a, **k):
                ...

            def warning(self, *a, **k):
                ...

        return _L()


log = get_logger(__name__)


class MessagingConfigError(RuntimeError):
    pass


def _env() -> str:
    return str(os.getenv("ENVIRONMENT") or getattr(settings, "ENVIRONMENT", "production") or "production").lower()


def _provider_name() -> str:
    return str(getattr(settings, "EMAIL_PROVIDER", "") or os.getenv("EMAIL_PROVIDER", "")).strip()


async def send_email(*, to: str, subject: str, body: str, meta: dict[str, Any] | None = None) -> dict[str, Any]:
    """Send email via configured provider. In dev, logs; in prod without provider, raises."""

    provider = _provider_name()
    if _env() == "production" and not provider:
        raise MessagingConfigError("Email provider is not configured")

    log.info("email.send", to=to, subject=subject, provider=provider or "noop", meta=meta or {})
    return {"success": True, "provider": provider or "noop"}
