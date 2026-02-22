from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import resolve_async_database_url, settings
from app.core.logging import get_logger
from app.integrations.kaspi_adapter import KaspiAdapter, KaspiAdapterError
from app.models.company import Company
from app.models.kaspi_feed_export import KaspiFeedExport
from app.models.kaspi_feed_upload import KaspiFeedUpload
from app.models.marketplace import KaspiStoreToken
from app.services.kaspi_feed_upload_service import (
    compute_next_attempt_at,
    normalize_kaspi_payload,
    update_feed_upload_job,
)

logger = get_logger(__name__)

_KASPI_FEED_UPLOAD_POLL_LOCK_KEY = 0x4B465550  # "KFUP"

_PENDING_STATUSES = {"created", "pending", "uploaded", "received"}
_SUCCESS_STATUSES = {"done", "success", "completed", "published"}
_FAILED_STATUSES = {"failed", "error"}


def _utcnow() -> datetime:
    return datetime.utcnow()


def _build_feed_upload_env(token: str) -> dict[str, str]:
    base_url = getattr(settings, "KASPI_FEED_BASE_URL", "https://kaspi.kz")
    upload_url = getattr(settings, "KASPI_FEED_UPLOAD_URL", None)
    status_url = getattr(settings, "KASPI_FEED_STATUS_URL", None)
    result_url = getattr(settings, "KASPI_FEED_RESULT_URL", None)
    base_url = base_url or "https://kaspi.kz"
    upload_url = upload_url or f"{base_url.rstrip('/')}/shop/api/feeds/import"
    status_url = status_url or f"{base_url.rstrip('/')}/shop/api/feeds/import/status"
    result_url = result_url or f"{base_url.rstrip('/')}/shop/api/feeds/import/result"
    return {
        "KASPI_FEED_UPLOAD_URL": upload_url,
        "KASPI_FEED_STATUS_URL": status_url,
        "KASPI_FEED_RESULT_URL": result_url,
        "KASPI_FEED_TOKEN": token,
        "KASPI_TOKEN": token,
    }


def _normalize_response(payload: Any) -> dict[str, Any]:
    if isinstance(payload, dict):
        return payload
    if isinstance(payload, list):
        return {"data": payload}
    if payload is None:
        return {}
    return {"raw": payload}


async def _try_poll_lock(session: AsyncSession) -> bool:
    res = await session.execute(text("SELECT pg_try_advisory_lock(:k)").bindparams(k=_KASPI_FEED_UPLOAD_POLL_LOCK_KEY))
    return bool(res.scalar_one_or_none())


async def _release_poll_lock(session: AsyncSession) -> None:
    await session.execute(text("SELECT pg_advisory_unlock(:k)").bindparams(k=_KASPI_FEED_UPLOAD_POLL_LOCK_KEY))


async def _fetch_due_uploads(session: AsyncSession, *, limit: int) -> list[KaspiFeedUpload]:
    now = _utcnow()
    stmt = (
        select(KaspiFeedUpload)
        .where(KaspiFeedUpload.status.in_(list(_PENDING_STATUSES)))
        .where(or_(KaspiFeedUpload.next_attempt_at.is_(None), KaspiFeedUpload.next_attempt_at <= now))
        .order_by(KaspiFeedUpload.next_attempt_at.asc().nullsfirst(), KaspiFeedUpload.created_at.asc())
        .limit(limit)
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def _handle_upload(upload_id: UUID | str, session_factory: sessionmaker) -> dict[str, Any]:
    async with session_factory() as session:
        upload = await session.get(KaspiFeedUpload, upload_id)
        if not upload:
            return {"status": "skipped"}

        company = await session.get(Company, upload.company_id)
        store_name = (company.kaspi_store_id or "").strip() if company else ""
        if not store_name:
            await update_feed_upload_job(
                session,
                job=upload,
                status="failed",
                error_code="kaspi_store_not_configured",
                error_message="kaspi_store_not_configured",
                next_attempt_at=None,
            )
            return {"status": "failed", "error": "kaspi_store_not_configured"}

        token = await KaspiStoreToken.get_token(session, store_name)
        if not token:
            await update_feed_upload_job(
                session,
                job=upload,
                status="failed",
                error_code="kaspi_token_not_found",
                error_message="kaspi_token_not_found",
                next_attempt_at=None,
            )
            return {"status": "failed", "error": "kaspi_token_not_found"}

        max_attempts = int(getattr(settings, "KASPI_FEED_UPLOAD_MAX_ATTEMPTS", 5) or 5)
        base_delay = int(getattr(settings, "KASPI_FEED_UPLOAD_BACKOFF_BASE_SECONDS", 30) or 30)
        max_delay = int(getattr(settings, "KASPI_FEED_UPLOAD_BACKOFF_MAX_SECONDS", 900) or 900)
        now = _utcnow()
        extra_env = _build_feed_upload_env(token)

        try:
            if not upload.import_code:
                export = await session.get(KaspiFeedExport, upload.export_id) if upload.export_id else None
                if not export or not export.payload_text:
                    await update_feed_upload_job(
                        session,
                        job=upload,
                        status="failed",
                        error_code="export_payload_missing",
                        error_message="export_payload_missing",
                        next_attempt_at=None,
                    )
                    return {"status": "failed", "error": "export_payload_missing"}

                tmp_dir = settings.tmp_dir()
                tmp_dir.mkdir(parents=True, exist_ok=True)
                tmp_path = tmp_dir / f"kaspi_feed_poll_{upload.company_id}_{upload.id}.xml"
                try:
                    tmp_path.write_text(export.payload_text, encoding="utf-8")
                    response = KaspiAdapter().feed_upload(
                        store_name,
                        str(tmp_path),
                        comment=upload.comment,
                        extra_env=extra_env,
                    )
                finally:
                    try:
                        if tmp_path.exists():
                            tmp_path.unlink()
                    except Exception:
                        logger.warning("Kaspi feed poll temp cleanup failed: path=%s", tmp_path)

                normalized = normalize_kaspi_payload(_normalize_response(response))
                import_code = normalized.get("importCode") or normalized.get("import_code")
                status_value = str(normalized.get("status") or "uploaded")
                upload.attempts = int(upload.attempts or 0) + 1
                upload.last_attempt_at = now
                await update_feed_upload_job(
                    session,
                    job=upload,
                    status=status_value,
                    import_code=str(import_code) if import_code else None,
                    error_code=None,
                    error_message=None,
                    response_json=normalized,
                    next_attempt_at=compute_next_attempt_at(
                        now=now,
                        attempts=upload.attempts,
                        base_delay_seconds=base_delay,
                        max_delay_seconds=max_delay,
                    ),
                )
                return {"status": "ok", "import_status": status_value}

            response = KaspiAdapter().feed_import_status(
                store_name,
                import_id=upload.import_code,
                extra_env=extra_env,
            )
            normalized = normalize_kaspi_payload(_normalize_response(response))
            status_value = str(normalized.get("status") or upload.status)
            status_lower = status_value.strip().lower()
            upload.attempts = int(upload.attempts or 0) + 1
            upload.last_attempt_at = now
            next_attempt_at = None
            if status_lower in _SUCCESS_STATUSES:
                next_attempt_at = None
            elif status_lower in _FAILED_STATUSES:
                next_attempt_at = None
            else:
                next_attempt_at = compute_next_attempt_at(
                    now=now,
                    attempts=upload.attempts,
                    base_delay_seconds=base_delay,
                    max_delay_seconds=max_delay,
                )
            await update_feed_upload_job(
                session,
                job=upload,
                status=status_value,
                error_code=None,
                error_message=None,
                response_json=normalized,
                next_attempt_at=next_attempt_at,
            )
            if status_lower in _SUCCESS_STATUSES:
                return {"status": "ok", "import_status": status_value}
            if status_lower in _FAILED_STATUSES:
                return {"status": "failed", "error": "feed_upload_failed"}
            return {"status": "ok", "import_status": status_value}
        except KaspiAdapterError as exc:
            upload.attempts = int(upload.attempts or 0) + 1
            upload.last_attempt_at = now
            status_value = "failed" if upload.attempts >= max_attempts else "pending"
            next_attempt_at = None
            if status_value == "pending":
                next_attempt_at = compute_next_attempt_at(
                    now=now,
                    attempts=upload.attempts,
                    base_delay_seconds=base_delay,
                    max_delay_seconds=max_delay,
                )
            await update_feed_upload_job(
                session,
                job=upload,
                status=status_value,
                error_code="upstream_unavailable",
                error_message=str(exc)[:500],
                next_attempt_at=next_attempt_at,
            )
            return {"status": "failed", "error": "upstream_unavailable"}
        except Exception as exc:  # pragma: no cover
            upload.attempts = int(upload.attempts or 0) + 1
            upload.last_attempt_at = now
            status_value = "failed" if upload.attempts >= max_attempts else "pending"
            next_attempt_at = None
            if status_value == "pending":
                next_attempt_at = compute_next_attempt_at(
                    now=now,
                    attempts=upload.attempts,
                    base_delay_seconds=base_delay,
                    max_delay_seconds=max_delay,
                )
            await update_feed_upload_job(
                session,
                job=upload,
                status=status_value,
                error_code="feed_upload_failed",
                error_message=str(exc)[:500],
                next_attempt_at=next_attempt_at,
            )
            return {"status": "failed", "error": "feed_upload_failed"}


async def run_kaspi_feed_upload_poll_async() -> dict[str, Any]:
    summary: dict[str, Any] = {"queued": 0, "processed": 0, "success": 0, "failed": 0, "skipped": 0}

    async_url, _source, _fp = resolve_async_database_url(settings)
    engine = create_async_engine(async_url, echo=False, pool_pre_ping=True)
    AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    lock_session = AsyncSessionLocal()
    try:
        acquired = await _try_poll_lock(lock_session)
        if not acquired:
            summary["skipped"] = 1
            return summary

        limit = int(getattr(settings, "KASPI_FEED_UPLOAD_BATCH_SIZE", 50) or 50)
        uploads = await _fetch_due_uploads(lock_session, limit=limit)
        summary["queued"] = len(uploads)
        if not uploads:
            return summary

        sem = asyncio.Semaphore(int(getattr(settings, "KASPI_FEED_UPLOAD_MAX_CONCURRENCY", 3) or 3))

        async def _wrapped(upload_id: str):
            async with sem:
                return await _handle_upload(upload_id, AsyncSessionLocal)

        tasks = [_wrapped(upload.id) for upload in uploads]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for result in results:
            summary["processed"] += 1
            if isinstance(result, Exception):
                summary["failed"] += 1
                continue
            if result.get("status") == "ok":
                summary["success"] += 1
            elif result.get("status") == "skipped":
                summary["skipped"] += 1
            else:
                summary["failed"] += 1
        logger.info(
            "kaspi_feed_upload_poll_done",
            extra={
                "queued": summary["queued"],
                "processed": summary["processed"],
                "success": summary["success"],
                "failed": summary["failed"],
            },
        )
        return summary
    finally:
        try:
            await _release_poll_lock(lock_session)
        except Exception:
            pass
        await lock_session.close()
        await engine.dispose()


def run_kaspi_feed_upload_poll() -> dict[str, Any]:
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    if loop.is_running():
        return loop.run_until_complete(run_kaspi_feed_upload_poll_async())
    return loop.run_until_complete(run_kaspi_feed_upload_poll_async())
