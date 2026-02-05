"""Messaging utilities (email)."""

from __future__ import annotations

import os
from typing import Any

from app.utils.pii import mask_email

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


async def _resolve_provider(db=None):
    from app.services.messaging_providers import MessagingProviderResolver

    if db is not None:
        return await MessagingProviderResolver.resolve(db, domain="messaging")

    from app.core.db import async_session_maker

    async with async_session_maker() as session:
        return await MessagingProviderResolver.resolve(session, domain="messaging")


async def send_email(
    *,
    to: str,
    subject: str,
    body: str,
    html_body: str | None = None,
    attachments: list[str] | None = None,
    cc: list[str] | None = None,
    bcc: list[str] | None = None,
    from_email: str | None = None,
    meta: dict[str, Any] | None = None,
    db=None,
) -> dict[str, Any]:
    """Send email via configured provider. In dev/test, noop is allowed."""

    try:
        provider = await _resolve_provider(db)
    except Exception as exc:
        raise MessagingConfigError("email_provider_not_configured") from exc

    metadata: dict[str, Any] = {
        "subject": subject,
        "html_body": html_body,
        "attachments": attachments or [],
        "cc": cc or [],
        "bcc": bcc or [],
    }
    if from_email:
        metadata["from_email"] = from_email
    if meta:
        metadata["meta"] = meta

    result = await provider.send_message(to=to, text=body, metadata=metadata)
    provider_name = result.get("provider") or getattr(provider, "name", "unknown")
    status = result.get("status")
    success = bool(result.get("success")) or status in {"ok", "sent", "noop"}

    log.info(
        "email.send",
        to=mask_email(to),
        subject=subject,
        provider=provider_name,
        status=status,
        meta=meta or {},
    )
    return {
        "success": success,
        "provider": provider_name,
        "status": status,
    }
