from __future__ import annotations

from datetime import datetime
from typing import Any

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_async_db
from app.core.dependencies import require_store_admin
from app.core.security import resolve_tenant_company_id
from app.models.integration_event import IntegrationEvent
from app.models.user import User

router = APIRouter(prefix="/api/v1/integrations", tags=["integrations"])


class IntegrationEventOut(BaseModel):
    id: int
    company_id: int
    merchant_uid: str | None = None
    kind: str
    status: str
    error_code: str | None = None
    error_message: str | None = None
    request_id: str | None = None
    occurred_at: datetime
    meta_json: dict[str, Any] | None = None


@router.get(
    "/events",
    summary="List integration events",
    response_model=list[IntegrationEventOut],
)
async def list_integration_events(
    kind: str | None = Query(None),
    limit: int = Query(100, ge=1, le=200),
    current_user: User = Depends(require_store_admin),
    session: AsyncSession = Depends(get_async_db),
) -> list[IntegrationEventOut]:
    company_id = resolve_tenant_company_id(current_user, not_found_detail="Company not set")

    stmt = sa.select(IntegrationEvent).where(IntegrationEvent.company_id == company_id)
    if kind:
        if kind == "kaspi":
            stmt = stmt.where(IntegrationEvent.kind.like("kaspi%"))
        else:
            stmt = stmt.where(IntegrationEvent.kind == kind)

    stmt = stmt.order_by(IntegrationEvent.occurred_at.desc()).limit(limit)
    rows = (await session.execute(stmt)).scalars().all()

    return [
        IntegrationEventOut(
            id=row.id,
            company_id=row.company_id,
            merchant_uid=row.merchant_uid,
            kind=row.kind,
            status=row.status,
            error_code=row.error_code,
            error_message=row.error_message,
            request_id=row.request_id,
            occurred_at=row.occurred_at,
            meta_json=row.meta_json,
        )
        for row in rows
    ]
