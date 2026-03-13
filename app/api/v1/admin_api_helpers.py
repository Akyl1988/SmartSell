from __future__ import annotations

from typing import Any

from app.models.campaign import Campaign


def build_campaign_queue_item_payload(campaign: Campaign) -> dict[str, Any]:
    return {
        "id": campaign.id,
        "company_id": campaign.company_id,
        "title": campaign.title,
        "processing_status": campaign.processing_status.value,
        "queued_at": campaign.queued_at.isoformat() if campaign.queued_at else None,
        "started_at": campaign.started_at.isoformat() if campaign.started_at else None,
        "finished_at": campaign.finished_at.isoformat() if campaign.finished_at else None,
        "failed_at": campaign.failed_at.isoformat() if campaign.failed_at else None,
        "next_attempt_at": campaign.next_attempt_at.isoformat() if campaign.next_attempt_at else None,
        "attempts": campaign.attempts,
        "last_error": campaign.last_error,
        "request_id": campaign.request_id,
        "requested_by_user_id": campaign.requested_by_user_id,
    }


def build_campaign_processing_payload(campaign: Campaign, request_id: str | None) -> dict[str, Any]:
    return {
        "campaign_id": campaign.id,
        "status": campaign.processing_status.value,
        "queued_at": campaign.queued_at.isoformat() if campaign.queued_at else None,
        "started_at": campaign.started_at.isoformat() if campaign.started_at else None,
        "finished_at": campaign.finished_at.isoformat() if campaign.finished_at else None,
        "failed_at": campaign.failed_at.isoformat() if campaign.failed_at else None,
        "next_attempt_at": campaign.next_attempt_at.isoformat() if campaign.next_attempt_at else None,
        "last_error": campaign.last_error,
        "attempts": campaign.attempts,
        "request_id": request_id,
    }


def build_campaign_transition_audit_meta(
    *,
    action: str,
    campaign: Campaign,
    admin_user_id: int | None,
    request_id: str,
    force: bool,
    prev_processing_status: str,
    prev_attempts: int,
    prev_last_error: str | None,
) -> dict[str, Any]:
    return {
        "action": action,
        "campaign_id": campaign.id,
        "admin_user_id": admin_user_id,
        "request_id": request_id,
        "force": bool(force),
        "prev_processing_status": prev_processing_status,
        "prev_attempts": prev_attempts,
        "prev_last_error": prev_last_error,
        "new_processing_status": campaign.processing_status.value,
        "new_attempts": campaign.attempts,
        "new_last_error": campaign.last_error,
    }
