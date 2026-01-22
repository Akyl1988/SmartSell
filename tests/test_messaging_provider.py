from __future__ import annotations

import secrets

import httpx
import pytest
from cryptography.fernet import Fernet
from httpx import MockTransport, Response
from sqlalchemy import select

from app.core.crypto import decrypt_json
from app.core.provider_registry import ProviderRegistry
from app.core.security import create_access_token, get_password_hash
from app.integrations.providers.webhook.messaging import WebhookMessagingProvider
from app.models.integration_provider import IntegrationProviderEvent
from app.models.integration_provider_config import IntegrationProviderConfig
from app.models.user import User, UserSession
from app.services.messaging_providers import MessagingProviderResolver


@pytest.fixture(autouse=True)
def _setup_master_key(monkeypatch):
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("INTEGRATIONS_MASTER_KEY", key)
    yield


async def _make_admin(async_db_session):
    user = User(
        username="admin_msg",
        email="admin_msg@example.com",
        phone="+77000003001",
        hashed_password=get_password_hash("Secret123!"),
        role="platform_admin",
        is_active=True,
        is_verified=True,
    )
    async_db_session.add(user)
    await async_db_session.commit()
    await async_db_session.refresh(user)
    session = UserSession(
        user_id=user.id,
        refresh_token=f"rt-{user.id}-{secrets.token_urlsafe(8)}",
        is_active=True,
    )
    async_db_session.add(session)
    await async_db_session.commit()
    await async_db_session.refresh(session)
    token = create_access_token(subject=user.id, extra={"role": user.role, "sid": session.id})
    return user, token


@pytest.mark.asyncio
async def test_messaging_config_redaction(async_client, async_db_session):
    _, token = await _make_admin(async_db_session)
    headers = {"Authorization": f"Bearer {token}"}

    await async_client.post(
        "/api/admin/integrations/providers",
        headers=headers,
        json={"domain": "messaging", "provider": "webhook", "config": {}, "is_enabled": True},
    )

    resp_put = await async_client.put(
        "/api/admin/integrations/messaging/config",
        headers=headers,
        json={"provider": "webhook", "config": {"url": "https://hook.test", "api_key": "secret"}},
    )
    assert resp_put.status_code == 200, resp_put.text
    data_put = resp_put.json().get("config") or {}
    assert data_put.get("api_key") == "***"

    resp_get = await async_client.get(
        "/api/admin/integrations/messaging/config",
        headers=headers,
        params={"provider": "webhook"},
    )
    assert resp_get.status_code == 200, resp_get.text
    data_get = resp_get.json().get("config") or {}
    assert data_get.get("api_key") == "***"

    res = await async_db_session.execute(
        IntegrationProviderConfig.__table__.select().where(
            IntegrationProviderConfig.domain == "messaging",
            IntegrationProviderConfig.provider == "webhook",
        )
    )
    row = res.fetchone()
    assert row is not None
    decrypted = decrypt_json(row.config_encrypted)
    assert decrypted.get("api_key") == "secret"


@pytest.mark.asyncio
async def test_messaging_healthcheck_with_redis_down(monkeypatch, async_client, async_db_session):
    admin, token = await _make_admin(async_db_session)
    headers = {"Authorization": f"Bearer {token}"}

    await async_client.post(
        "/api/admin/integrations/providers",
        headers=headers,
        json={"domain": "messaging", "provider": "webhook", "config": {}, "is_enabled": True},
    )
    await async_client.put(
        "/api/admin/integrations/messaging/config",
        headers=headers,
        json={"provider": "webhook", "config": {"url": "https://hook.test/health", "api_key": "k"}},
    )

    def _raise(*_args, **_kwargs):
        raise RuntimeError("redis_down")

    transport = MockTransport(lambda request: Response(200, json={"ok": True}))
    real_async_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **kw: real_async_client(*a, transport=transport, **kw))
    monkeypatch.setattr(ProviderRegistry, "_redis_client", staticmethod(_raise))

    resp = await async_client.get(
        "/api/admin/integrations/messaging/healthcheck",
        headers=headers,
        params={"provider": "webhook"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data.get("status") in {"ok", "error"}

    res_event = await async_db_session.execute(
        select(IntegrationProviderEvent)
        .where(IntegrationProviderEvent.domain == "messaging")
        .order_by(IntegrationProviderEvent.id.desc())
    )
    evt = res_event.scalars().first()
    assert evt is not None
    assert (evt.meta_json or {}).get("actor_email") == admin.email


@pytest.mark.asyncio
async def test_messaging_hot_switch_and_events(monkeypatch, async_client, async_db_session):
    MessagingProviderResolver.reset_cache()
    ProviderRegistry.invalidate()
    admin, token = await _make_admin(async_db_session)
    headers = {"Authorization": f"Bearer {token}"}

    transport_send = MockTransport(lambda request: Response(200, json={"sent": True}))
    real_async_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **kw: real_async_client(*a, transport=transport_send, **kw))

    for provider, is_active in (("noop", True), ("webhook", False)):
        resp = await async_client.post(
            "/api/admin/integrations/providers",
            headers=headers,
            json={
                "domain": "messaging",
                "provider": provider,
                "config": {},
                "is_enabled": True,
                "is_active": is_active,
            },
        )
        assert resp.status_code in {200, 201}, resp.text

    resp_cfg = await async_client.put(
        "/api/admin/integrations/messaging/config",
        headers=headers,
        json={"provider": "webhook", "config": {"url": "https://hook.test/send", "api_key": "k"}},
    )
    assert resp_cfg.status_code == 200, resp_cfg.text

    resp_activate = await async_client.post(
        "/api/admin/integrations/active",
        headers=headers,
        json={"domain": "messaging", "provider": "webhook"},
    )
    assert resp_activate.status_code == 200, resp_activate.text

    provider = await MessagingProviderResolver.resolve(async_db_session, domain="messaging")
    send_resp = await provider.send_message("+7700003300", "hi", metadata={"k": "v"})
    assert send_resp.get("provider") == "webhook"
    assert send_resp.get("status") == "ok"

    resp_activate_noop = await async_client.post(
        "/api/admin/integrations/active",
        headers=headers,
        json={"domain": "messaging", "provider": "noop"},
    )
    assert resp_activate_noop.status_code == 200, resp_activate_noop.text

    provider_noop = await MessagingProviderResolver.resolve(async_db_session, domain="messaging")
    noop_resp = await provider_noop.send_message("+7700003301", "hi2")
    assert noop_resp.get("provider", "").startswith("noop")

    res_event = await async_db_session.execute(
        select(IntegrationProviderEvent)
        .where(IntegrationProviderEvent.domain == "messaging")
        .order_by(IntegrationProviderEvent.id.desc())
    )
    evt = res_event.scalars().first()
    assert evt is not None
    assert (evt.meta_json or {}).get("actor_email") == admin.email


@pytest.mark.asyncio
async def test_webhook_provider_healthcheck_smoke():
    provider = WebhookMessagingProvider(config={"url": "https://example.test"})
    assert provider.name == "webhook"
    assert provider.timeout_seconds == 5.0
    assert provider.retries == 2
