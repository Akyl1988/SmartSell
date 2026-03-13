from __future__ import annotations

import logging
import os
from typing import Any

from app.api.v1.campaigns_storage import InMemoryStorage
from app.models.campaign import Campaign as DbCampaign
from app.models.campaign import CampaignProcessingStatus as DbCampaignProcessingStatus
from app.models.campaign import CampaignStatus as DbCampaignStatus
from app.models.campaign import Message as DbMessage

logger = logging.getLogger(__name__)

_STORAGE_BACKEND: str | None = None
_STORAGE_INSTANCE: Any | None = None


def truthy_env(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


def resolve_campaigns_storage_backend() -> str:
    if truthy_env("FORCE_INMEMORY_BACKENDS"):
        return "memory"

    raw = (os.getenv("SMARTSELL_CAMPAIGNS_STORAGE") or "").strip().lower()
    if raw in {"orm", "memory", "legacy_sql"}:
        return raw
    if raw == "sql":
        return "legacy_sql"

    if truthy_env("SMARTSELL_USE_SQL"):
        return "legacy_sql"

    return "orm"


def normalize_campaigns_db_url() -> str | None:
    raw = os.getenv("DATABASE_URL") or os.getenv("DB_URL") or ""
    url = raw.strip()
    if not url:
        return None
    if url.startswith("postgresql+asyncpg://"):
        return url.replace("postgresql+asyncpg://", "postgresql+psycopg2://", 1)
    if url.startswith("postgresql+psycopg://"):
        return url.replace("postgresql+psycopg://", "postgresql+psycopg2://", 1)
    return url


def init_campaigns_storage() -> Any:
    global _STORAGE_BACKEND, _STORAGE_INSTANCE
    if _STORAGE_INSTANCE is not None:
        return _STORAGE_INSTANCE

    backend = resolve_campaigns_storage_backend()
    if backend == "legacy_sql":
        try:
            from app.storage.campaigns_sql import CampaignsStorageSQL  # type: ignore

            _STORAGE_INSTANCE = CampaignsStorageSQL(db_url=normalize_campaigns_db_url())
            _STORAGE_BACKEND = "legacy_sql"
            return _STORAGE_INSTANCE
        except Exception as exc:
            logger.warning("Campaigns SQL storage unavailable; falling back to memory: %s", exc)

    _STORAGE_INSTANCE = InMemoryStorage()
    _STORAGE_BACKEND = backend if backend in {"orm", "memory"} else "memory"
    return _STORAGE_INSTANCE


def get_campaigns_storage() -> Any:
    return init_campaigns_storage()


def get_campaigns_storage_backend() -> str:
    if _STORAGE_BACKEND is not None:
        return _STORAGE_BACKEND
    return resolve_campaigns_storage_backend()


ORM_ACTIVE_STATUSES = [
    DbCampaignStatus.ACTIVE,
    DbCampaignStatus.READY,
    DbCampaignStatus.SCHEDULED,
    DbCampaignStatus.RUNNING,
    DbCampaignStatus.SUCCESS,
]


def orm_status_is_active(status: DbCampaignStatus | None) -> bool:
    if status is None:
        return False
    return status in ORM_ACTIVE_STATUSES


def orm_message_to_payload(message: DbMessage) -> dict[str, Any]:
    return {
        "id": message.id,
        "recipient": message.recipient,
        "content": message.content,
        "status": message.status.value,
        "channel": message.channel.value,
        "scheduled_for": None,
        "error": message.error_message,
    }


def orm_campaign_to_payload(campaign: DbCampaign, messages: list[DbMessage]) -> dict[str, Any]:
    processing_status = campaign.processing_status
    if processing_status is None:
        processing_status = DbCampaignProcessingStatus.DONE
    return {
        "id": campaign.id,
        "title": campaign.title,
        "description": campaign.description,
        "active": orm_status_is_active(campaign.status),
        "archived": campaign.deleted_at is not None,
        "company_id": campaign.company_id,
        "tags": [],
        "messages": [orm_message_to_payload(message) for message in messages],
        "created_at": campaign.created_at.isoformat() if campaign.created_at else None,
        "updated_at": campaign.updated_at.isoformat() if campaign.updated_at else None,
        "schedule": campaign.scheduled_at.isoformat() if campaign.scheduled_at else None,
        "owner": None,
        "processing_status": processing_status.value,
        "queued_at": campaign.queued_at.isoformat() if campaign.queued_at else None,
        "started_at": campaign.started_at.isoformat() if campaign.started_at else None,
        "finished_at": campaign.finished_at.isoformat() if campaign.finished_at else None,
        "failed_at": campaign.failed_at.isoformat() if campaign.failed_at else None,
        "next_attempt_at": campaign.next_attempt_at.isoformat() if campaign.next_attempt_at else None,
        "last_error": campaign.last_error,
        "attempts": int(campaign.attempts or 0),
        "request_id": campaign.request_id,
    }


def build_campaign_queue_response(campaign: DbCampaign, request_id: str | None) -> dict[str, Any]:
    return {
        "campaign_id": campaign.id,
        "status": campaign.processing_status.value,
        "queued_at": campaign.queued_at.isoformat() if campaign.queued_at else None,
        "started_at": campaign.started_at.isoformat() if campaign.started_at else None,
        "finished_at": campaign.finished_at.isoformat() if campaign.finished_at else None,
        "failed_at": campaign.failed_at.isoformat() if campaign.failed_at else None,
        "last_error": campaign.last_error,
        "attempts": campaign.attempts,
        "request_id": request_id,
    }


def sync_storage_campaign_processing(
    *,
    storage: Any,
    campaign_id: int,
    campaign: DbCampaign,
    now_iso: str,
) -> None:
    stored = storage.get_campaign(campaign_id)
    if not stored:
        return
    stored["processing_status"] = campaign.processing_status.value
    stored["queued_at"] = campaign.queued_at.isoformat() if campaign.queued_at else None
    stored["started_at"] = campaign.started_at.isoformat() if campaign.started_at else None
    stored["finished_at"] = campaign.finished_at.isoformat() if campaign.finished_at else None
    stored["failed_at"] = campaign.failed_at.isoformat() if campaign.failed_at else None
    stored["next_attempt_at"] = campaign.next_attempt_at.isoformat() if campaign.next_attempt_at else None
    stored["last_error"] = campaign.last_error
    stored["attempts"] = int(campaign.attempts or 0)
    stored["request_id"] = campaign.request_id
    stored["updated_at"] = now_iso
    storage.save_campaign(stored)


__all__ = [
    "ORM_ACTIVE_STATUSES",
    "build_campaign_queue_response",
    "get_campaigns_storage",
    "get_campaigns_storage_backend",
    "init_campaigns_storage",
    "normalize_campaigns_db_url",
    "orm_campaign_to_payload",
    "orm_message_to_payload",
    "orm_status_is_active",
    "resolve_campaigns_storage_backend",
    "sync_storage_campaign_processing",
    "truthy_env",
]
