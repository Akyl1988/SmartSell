from __future__ import annotations

import os
from datetime import datetime, timedelta
from hashlib import sha256
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.kaspi_feed_export import KaspiFeedExport
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
    payload_hash: str | None = None,
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
        payload_hash=payload_hash,
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
    response_json: dict[str, Any] | None = None,
    next_attempt_at: datetime | None = None,
) -> KaspiFeedUpload:
    if status is not None:
        job.status = status
    if import_code is not None:
        job.import_code = import_code
    if response_json is not None:
        job.response_json = response_json
    job.last_error_code = error_code
    job.last_error_message = error_message
    if last_attempt_at is not None:
        job.last_attempt_at = last_attempt_at
    if next_attempt_at is not None:
        job.next_attempt_at = next_attempt_at
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


def _extract_error_text(payload: dict[str, Any] | None) -> str | None:
    if not payload:
        return None
    for key in ("detail", "errorMessage", "error_message", "message", "error"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _looks_like_unsupported_content_type(message: str | None) -> bool:
    if not message:
        return False
    text = message.strip().lower()
    if "content type" in text and "not supported" in text:
        return True
    return "unsupported media type" in text


def is_unsupported_content_type_error(
    *,
    error_message: str | None,
    response_payload: dict[str, Any] | None = None,
) -> bool:
    return _looks_like_unsupported_content_type(error_message) or _looks_like_unsupported_content_type(
        _extract_error_text(response_payload)
    )


def is_feed_upload_url_misconfigured(upload_url: str | None) -> bool:
    if not upload_url:
        return False
    return "/shop/api/products/import" in upload_url.lower()


def should_block_feed_upload_url(upload_url: str | None) -> bool:
    if not is_feed_upload_url_misconfigured(upload_url):
        return False
    env_name = (os.environ.get("ENVIRONMENT", "") or "").lower()
    debug_flag = os.environ.get("DEBUG", "").lower() in {"1", "true", "yes", "on"}
    if debug_flag:
        return False
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return False
    if env_name in {"local", "development", "dev", "test", "testing"}:
        return False
    return True


def compute_feed_payload_hash(xml_body: str) -> str:
    return sha256(xml_body.encode("utf-8")).hexdigest()


def compute_next_attempt_at(
    *,
    now: datetime,
    attempts: int,
    base_delay_seconds: int,
    max_delay_seconds: int,
) -> datetime:
    delay = base_delay_seconds * (2 ** max(attempts - 1, 0))
    delay = min(max_delay_seconds, max(1, delay))
    return now + timedelta(seconds=delay)


async def find_recent_successful_upload_by_hash(
    session: AsyncSession,
    *,
    company_id: int,
    merchant_uid: str,
    payload_hash: str,
    window_hours: int = 24,
) -> KaspiFeedUpload | None:
    cutoff = datetime.utcnow() - timedelta(hours=window_hours)
    success_statuses = {"done", "success", "completed", "published"}
    result = await session.execute(
        sa.select(KaspiFeedUpload)
        .where(
            KaspiFeedUpload.company_id == company_id,
            KaspiFeedUpload.merchant_uid == merchant_uid,
            KaspiFeedUpload.payload_hash == payload_hash,
            KaspiFeedUpload.created_at >= cutoff,
            KaspiFeedUpload.status.in_(list(success_statuses)),
        )
        .order_by(KaspiFeedUpload.created_at.desc())
        .limit(1)
    )
    return result.scalars().first()


async def get_or_create_feed_export(
    session: AsyncSession,
    *,
    company_id: int,
    kind: str,
    xml_body: str,
) -> KaspiFeedExport:
    checksum = compute_feed_payload_hash(xml_body)
    existing = await session.execute(
        sa.select(KaspiFeedExport).where(
            KaspiFeedExport.company_id == company_id,
            KaspiFeedExport.kind == kind,
            KaspiFeedExport.checksum == checksum,
        )
    )
    record = existing.scalars().first()
    if record:
        return record

    record = KaspiFeedExport(
        company_id=company_id,
        kind=kind,
        format="xml",
        status="generated",
        checksum=checksum,
        payload_text=xml_body,
        stats_json=None,
    )
    session.add(record)
    await session.commit()
    await session.refresh(record)
    return record
