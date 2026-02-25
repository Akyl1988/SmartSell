from __future__ import annotations

import importlib

import pytest
from sqlalchemy import select

from app.api.v1 import campaigns as campaigns_api
from app.core.subscriptions.plan_catalog import normalize_plan_id
from app.models.billing import Subscription


def _reset_campaigns_storage() -> None:
    campaigns_mod = importlib.import_module("app.api.v1.campaigns")
    campaigns_mod._STORAGE_INSTANCE = None
    campaigns_mod._STORAGE_BACKEND = None


async def _ensure_subscription(async_db_session, *, company_id: int) -> None:
    subscription = (
        await async_db_session.execute(
            select(Subscription)
            .where(Subscription.company_id == company_id)
            .where(Subscription.deleted_at.is_(None))
            .limit(1)
        )
    ).scalar_one_or_none()
    if subscription:
        subscription.status = "active"
        return

    async_db_session.add(
        Subscription(
            company_id=company_id,
            plan=normalize_plan_id("start") or "trial",
            status="active",
            billing_cycle="monthly",
            price=0,
            currency="KZT",
        )
    )


GUARDED_ENDPOINTS = [
    ("get", "/api/v1/campaigns/search", None),
    ("get", "/api/v1/campaigns/recipients", None),
    ("get", "/api/v1/campaigns/search_tags?q=sale", None),
    ("get", "/api/v1/campaigns/export", None),
    ("get", "/api/v1/campaigns/export/json", None),
    (
        "post",
        "/api/v1/campaigns/import",
        [
            {
                "title": "Import campaign",
                "messages": [{"recipient": "user@example.com", "content": "Hello"}],
            }
        ],
    ),
    (
        "post",
        "/api/v1/campaigns/draft",
        {"title": "Draft campaign", "messages": [{"recipient": "user@example.com", "content": "Hello"}]},
    ),
    ("get", "/api/v1/campaigns/drafts", None),
    ("get", "/api/v1/campaigns/drafts/1", None),
    ("put", "/api/v1/campaigns/1", {"title": "Updated"}),
    ("delete", "/api/v1/campaigns/1", None),
    ("post", "/api/v1/campaigns/1/archive", None),
    ("post", "/api/v1/campaigns/1/restore", None),
    ("post", "/api/v1/campaigns/bulk_archive", {"ids": [1]}),
    ("post", "/api/v1/campaigns/bulk_restore", {"ids": [1]}),
    ("post", "/api/v1/campaigns/bulk_delete", {"ids": [1]}),
    (
        "post",
        "/api/v1/campaigns/1/messages",
        {"recipient": "user@example.com", "content": "Hello"},
    ),
    ("get", "/api/v1/campaigns/1/messages", None),
    ("get", "/api/v1/campaigns/1/messages/1", None),
    (
        "put",
        "/api/v1/campaigns/1/messages/1",
        {"recipient": "user@example.com", "content": "Updated"},
    ),
    ("delete", "/api/v1/campaigns/1/messages/1", None),
    (
        "post",
        "/api/v1/campaigns/1/messages/upsert_by_recipient",
        {"recipient": "user@example.com", "content": "Hello"},
    ),
    (
        "post",
        "/api/v1/campaigns/1/messages/1/status",
        {"status": "sent"},
    ),
    ("post", "/api/v1/campaigns/1/messages/1/reset_to_pending", None),
    ("post", "/api/v1/campaigns/1/messages/clear_failed", None),
    ("post", "/api/v1/campaigns/1/messages/mark_all_sent", None),
    (
        "post",
        "/api/v1/campaigns/1/messages/bulk_status_update",
        {"status": "sent", "ids": [1]},
    ),
    ("post", "/api/v1/campaigns/1/messages/bulk_delete", {"ids": [1]}),
    (
        "post",
        "/api/v1/campaigns/1/messages/bulk_add",
        {"messages": [{"recipient": "user@example.com", "content": "Hello"}]},
    ),
    (
        "post",
        "/api/v1/campaigns/1/messages/bulk_upsert",
        {"items": [{"recipient": "user@example.com", "content": "Hello"}]},
    ),
    ("get", "/api/v1/campaigns/1/stats", None),
    ("post", "/api/v1/campaigns/1/send", None),
    ("post", "/api/v1/campaigns/1/send_async", None),
    (
        "post",
        "/api/v1/campaigns/1/schedule",
        {"schedule_time": "2099-01-01T00:00:00Z"},
    ),
    ("post", "/api/v1/campaigns/1/cancel_schedule", None),
    ("post", "/api/v1/campaigns/1/preview_send", ["user@example.com"]),
    ("post", "/api/v1/campaigns/1/resend_failed", None),
    ("get", "/api/v1/campaigns/1/tags", None),
    ("post", "/api/v1/campaigns/1/tags", {"tag": "sale"}),
    ("delete", "/api/v1/campaigns/1/tags/sale", None),
    ("put", "/api/v1/campaigns/1/tags", {"tags": ["sale"]}),
    ("get", "/api/v1/campaigns/1/advanced_stats", None),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("method,path,payload", GUARDED_ENDPOINTS)
async def test_campaigns_storage_only_endpoints_guarded_in_orm(
    async_client,
    async_db_session,
    company_a_admin_headers,
    monkeypatch,
    method,
    path,
    payload,
):
    monkeypatch.setenv("SMARTSELL_CAMPAIGNS_STORAGE", "orm")
    monkeypatch.setenv("FORCE_INMEMORY_BACKENDS", "0")
    _reset_campaigns_storage()

    await _ensure_subscription(async_db_session, company_id=1001)
    await async_db_session.commit()

    request = getattr(async_client, method)
    if payload is None:
        resp = await request(path, headers=company_a_admin_headers)
    else:
        resp = await request(path, headers=company_a_admin_headers, json=payload)

    assert resp.status_code == campaigns_api.CAMPAIGNS_ORM_GUARD_STATUS, resp.text
    assert resp.json().get("detail") == campaigns_api.CAMPAIGNS_ORM_GUARD_DETAIL
