from __future__ import annotations

from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.kaspi_feed_upload import KaspiFeedUpload


async def get_feed_upload_by_request_id(
    session: AsyncSession,
    *,
    company_id: int,
    request_id: str | None,
) -> KaspiFeedUpload | None:
    if not request_id:
        return None
    result = await session.execute(
        sa.select(KaspiFeedUpload).where(
            KaspiFeedUpload.company_id == company_id,
            KaspiFeedUpload.request_id == request_id,
        )
    )
    return result.scalars().first()


async def create_feed_upload_job(
    session: AsyncSession,
    *,
    company_id: int,
    merchant_uid: str,
    export_id: int | None = None,
    source: str,
    request_id: str | None,
    comment: str | None = None,
) -> KaspiFeedUpload:
    now = datetime.utcnow()
    job = KaspiFeedUpload(
        company_id=company_id,
        merchant_uid=merchant_uid,
        export_id=export_id,
        source=source,
        comment=comment,
        status="created",
        attempts=0,
        request_id=request_id,
        created_at=now,
        updated_at=now,
    )
    session.add(job)
    await session.commit()
    await session.refresh(job)
    return job


async def mark_feed_upload_attempt(
    session: AsyncSession,
    *,
    job: KaspiFeedUpload,
) -> KaspiFeedUpload:
    job.last_attempt_at = datetime.utcnow()
    job.attempts = int(job.attempts or 0) + 1
    job.updated_at = datetime.utcnow()
    await session.commit()
    await session.refresh(job)
    return job


async def update_feed_upload_job(
    session: AsyncSession,
    *,
    job: KaspiFeedUpload,
    status: str | None = None,
    import_code: str | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
    last_attempt_at: datetime | None = None,
) -> KaspiFeedUpload:
    if status is not None:
        job.status = status
    if import_code is not None:
        job.import_code = import_code
    job.last_error_code = error_code
    job.last_error_message = error_message
    if last_attempt_at is not None:
        job.last_attempt_at = last_attempt_at
    job.updated_at = datetime.utcnow()
    await session.commit()
    await session.refresh(job)
    return job


def normalize_kaspi_payload(payload: Any) -> dict[str, Any]:
    if isinstance(payload, dict):
        if isinstance(payload.get("result"), dict):
            return payload["result"]
        return payload
    if isinstance(payload, list):
        return {"data": payload}
    return {"raw": payload}
