from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.integration_event import IntegrationEvent


async def record_integration_event(
    session: AsyncSession,
    *,
    company_id: int,
    merchant_uid: str | None,
    kind: str,
    status: str,
    error_code: str | None = None,
    error_message: str | None = None,
    request_id: str | None = None,
    occurred_at: datetime | None = None,
    meta_json: dict[str, Any] | None = None,
    commit: bool = True,
) -> IntegrationEvent:
    msg = (error_message or "").strip()
    event = IntegrationEvent(
        company_id=company_id,
        merchant_uid=merchant_uid,
        kind=kind,
        status=status,
        error_code=error_code,
        error_message=msg or None,
        request_id=request_id,
        occurred_at=occurred_at or datetime.utcnow(),
        meta_json=meta_json,
    )
    session.add(event)
    if commit:
        await session.commit()
        await session.refresh(event)
    return event
