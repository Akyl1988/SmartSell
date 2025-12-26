from __future__ import annotations

import time

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import select

from app.core.config import settings
from app.core.crypto import decrypt_json, encrypt_json, reset_crypto_key_cache
from app.core.provider_registry import CachedProvider, ProviderRegistry
from app.core.security import create_access_token, get_password_hash
from app.models.integration_provider import IntegrationProvider, IntegrationProviderEvent
from app.models.user import User


@pytest.fixture(autouse=True)
def _setup_master_key(monkeypatch):
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("INTEGRATIONS_MASTER_KEY", key)
    settings.INTEGRATIONS_MASTER_KEY = key
    reset_crypto_key_cache()
    try:
        yield
    finally:
        reset_crypto_key_cache()


@pytest.fixture(autouse=True)
def _reset_provider_registry():
    ProviderRegistry.invalidate()
    yield
    ProviderRegistry.invalidate()


@pytest.mark.asyncio
async def test_crypto_roundtrip():
    data = {"a": 1, "secret": "value"}
    token = encrypt_json(data)
    assert token != b""
    decoded = decrypt_json(token)
    assert decoded == data


async def _make_admin(async_db_session):
    user = User(
        username="admin_sys",
        email="admin_sys@example.com",
        phone="+77000000002",
        hashed_password=get_password_hash("Secret123!"),
        role="platform_admin",
        is_active=True,
        is_verified=True,
    )
    async_db_session.add(user)
    await async_db_session.commit()
    await async_db_session.refresh(user)
    token = create_access_token(subject=user.id)
    return user, token


@pytest.mark.asyncio
async def test_admin_switch_provider_publishesFC(async_client, async_db_session, monkeypatch):
    _, token = await _make_admin(async_db_session)
    headers = {"Authorization": f"Bearer {token}"}

    publish_calls: dict[str, str | int | None] = {}

    async def fake_publish(domain: str, version: int | None = None):
        publish_calls["domain"] = domain
        publish_calls["version"] = version

    async def fake_listener():
        return None

    monkeypatch.setattr(ProviderRegistry, "publish_change", staticmethod(fake_publish))
    monkeypatch.setattr(ProviderRegistry, "_ensure_listener", staticmethod(fake_listener))

    ProviderRegistry._cache["otp"] = CachedProvider(
        provider="noop-old",
        config={},
        version=1,
        cached_at=time.monotonic(),
    )

    resp = await async_client.post(
        "/api/admin/integrations/providers",
        json={
            "domain": "otp",
            "provider": "noop",
            "config": {"from": "+10000000000"},
            "is_enabled": True,
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text

    resp = await async_client.post(
        "/api/admin/integrations/active",
        json={"domain": "otp", "provider": "noop"},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text

    res = await async_db_session.execute(
        select(IntegrationProvider).where(
            IntegrationProvider.domain == "otp", IntegrationProvider.provider == "noop"
        )
    )
    active = res.scalar_one_or_none()
    assert active is not None
    assert active.provider == "noop"
    assert active.is_active is True

    res_event = await async_db_session.execute(
        select(IntegrationProviderEvent)
        .where(IntegrationProviderEvent.domain == "otp")
        .order_by(IntegrationProviderEvent.id.desc())
    )
    evt = res_event.scalar_one_or_none()
    assert evt is not None
    assert evt.provider_to == "noop"

    # publish + cache invalidated
    assert publish_calls.get("domain") == "otp"
    assert publish_calls.get("version") == active.version
    assert "otp" not in ProviderRegistry._cache


@pytest.mark.asyncio
async def test_access_control_admin_only(async_client, async_db_session):
    # create non-admin user
    user = User(
        username="manager1",
        email="manager1@example.com",
        phone="+77000000003",
        hashed_password=get_password_hash("Secret123!"),
        role="manager",
        is_active=True,
        is_verified=True,
    )
    async_db_session.add(user)
    await async_db_session.commit()
    await async_db_session.refresh(user)
    token = create_access_token(subject=user.id)

    resp = await async_client.get(
        "/api/admin/integrations/providers", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_single_active_enforced_with_events(async_client, async_db_session):
    _, token = await _make_admin(async_db_session)
    headers = {"Authorization": f"Bearer {token}"}

    resp = await async_client.post(
        "/api/admin/integrations/providers",
        json={
            "domain": "payments",
            "provider": "noop-a",
            "config": {"key": "a"},
            "is_enabled": True,
            "is_active": True,
        },
        headers=headers,
    )
    assert resp.status_code == 201

    resp = await async_client.post(
        "/api/admin/integrations/providers",
        json={
            "domain": "payments",
            "provider": "noop-b",
            "config": {"key": "b"},
            "is_enabled": True,
        },
        headers=headers,
    )
    assert resp.status_code == 201

    resp = await async_client.post(
        "/api/admin/integrations/active",
        json={"domain": "payments", "provider": "noop-b"},
        headers=headers,
    )
    assert resp.status_code == 200

    res = await async_db_session.execute(
        select(IntegrationProvider)
        .where(IntegrationProvider.domain == "payments")
        .order_by(IntegrationProvider.provider)
    )
    providers = res.scalars().all()
    active_names = [p.provider for p in providers if p.is_active]
    assert active_names == ["noop-b"]

    res_event = await async_db_session.execute(
        select(IntegrationProviderEvent)
        .where(IntegrationProviderEvent.domain == "payments")
        .order_by(IntegrationProviderEvent.id)
    )
    events = res_event.scalars().all()
    assert len(events) >= 2
    assert events[-1].provider_from == "noop-a"
    assert events[-1].provider_to == "noop-b"


@pytest.mark.asyncio
async def test_set_active_is_idempotent(async_client, async_db_session):
    _, token = await _make_admin(async_db_session)
    headers = {"Authorization": f"Bearer {token}"}

    resp = await async_client.post(
        "/api/admin/integrations/providers",
        json={
            "domain": "otp",
            "provider": "noop-idem",
            "config": {},
        },
        headers=headers,
    )
    assert resp.status_code == 201

    idem_headers = {**headers, "Idempotency-Key": "abc-idem"}

    resp1 = await async_client.post(
        "/api/admin/integrations/active",
        json={"domain": "otp", "provider": "noop-idem"},
        headers=idem_headers,
    )
    resp2 = await async_client.post(
        "/api/admin/integrations/active",
        json={"domain": "otp", "provider": "noop-idem"},
        headers=idem_headers,
    )

    assert resp1.status_code == 200
    assert resp2.status_code == 200
    assert resp1.json()["version"] == resp2.json()["version"]

    res = await async_db_session.execute(
        select(IntegrationProviderEvent).where(IntegrationProviderEvent.domain == "otp")
    )
    events = res.scalars().all()
    assert len(events) == 1

    res_active = await async_db_session.execute(
        select(IntegrationProvider).where(
            IntegrationProvider.domain == "otp", IntegrationProvider.provider == "noop-idem"
        )
    )
    active = res_active.scalar_one_or_none()
    assert active is not None
    assert active.version == resp1.json()["version"]


@pytest.mark.asyncio
async def test_cannot_activate_disabled_provider(async_client, async_db_session):
    _, token = await _make_admin(async_db_session)
    headers = {"Authorization": f"Bearer {token}"}

    resp = await async_client.post(
        "/api/admin/integrations/providers",
        json={
            "domain": "messaging",
            "provider": "noop-disabled",
            "config": {},
            "is_enabled": False,
        },
        headers=headers,
    )
    assert resp.status_code == 201

    resp = await async_client.post(
        "/api/admin/integrations/active",
        json={"domain": "messaging", "provider": "noop-disabled"},
        headers=headers,
    )
    assert resp.status_code == 404
