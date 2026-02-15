from __future__ import annotations

from typing import Any
from uuid import uuid4

from app.core.logging import audit_logger


def log_campaign_event(
    *,
    event: str,
    message: str,
    request_id: str | None,
    company_id: int | None,
    campaign_id: int | None,
    run_id: int | str | None = None,
    status_before: str | None = None,
    status_after: str | None = None,
    attempt: int | None = None,
    meta: dict[str, Any] | None = None,
    level: str = "info",
) -> str:
    rid = request_id or str(uuid4())
    meta_safe: dict[str, Any] = dict(meta or {})
    meta_safe.update(
        {
            "request_id": rid,
            "company_id": company_id,
            "campaign_id": campaign_id,
            "run_id": run_id,
            "attempt": attempt,
            "status_before": status_before,
            "status_after": status_after,
        }
    )

    audit_logger.log_system_event(
        level=level,
        event=event,
        message=message,
        meta=meta_safe,
    )
    return rid
