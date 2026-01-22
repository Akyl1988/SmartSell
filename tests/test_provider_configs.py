from __future__ import annotations

import secrets

import pytest
from cryptography.fernet import Fernet
from httpx import AsyncClient

from app.core.crypto import decrypt_json, reset_crypto_key_cache
from app.core.provider_registry import ProviderRegistry
from app.core.security import create_access_token, get_password_hash
from app.models.integration_provider_config import IntegrationProviderConfig
from app.models.user import User, UserSession
from app.services.integration_providers import IntegrationProviderService
from app.services.otp_providers import OtpProviderResolver
from app.services.provider_configs import ProviderConfigService


@pytest.fixture(autouse=True)
def _setup_master_key(monkeypatch):
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("INTEGRATIONS_MASTER_KEY", key)
    reset_crypto_key_cache()
    try:
        yield
    finally:
        reset_crypto_key_cache()


async def _make_admin(async_db_session):
    user = User(
        username="admin_cfg",
        email="admin_cfg@example.com",
        phone="+77000000011",
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
async def test_config_roundtrip_redacted(async_client: AsyncClient, async_db_session, monkeypatch):
    _, token = await _make_admin(async_db_session)
    headers = {"Authorization": f"Bearer {token}"}

    cfg_payload = {"api_key": "secret123", "nested": {"token": "abc"}}

    resp_put = await async_client.put(
        "/api/admin/integrations/providers/otp/noop/config",
        headers=headers,
        json={"config": cfg_payload},
    )
    assert resp_put.status_code == 200, resp_put.text
    data_put = resp_put.json().get("config") or {}
    assert data_put != cfg_payload
    assert data_put.get("api_key") == "***"

    resp_get = await async_client.get(
        "/api/admin/integrations/providers/otp/noop/config",
        headers=headers,
    )
    assert resp_get.status_code == 200, resp_get.text
    data_get = resp_get.json().get("config") or {}
    assert data_get.get("api_key") == "***"
    assert data_get.get("nested", {}).get("token") == "***"

    res = await async_db_session.execute(
        IntegrationProviderConfig.__table__.select().where(
            IntegrationProviderConfig.domain == "otp",
            IntegrationProviderConfig.provider == "noop",
        )
    )
    row = res.fetchone()
    assert row is not None
    encrypted = row.config_encrypted
    assert isinstance(encrypted, bytes | bytearray)
    assert b"secret123" not in encrypted
    decrypted = decrypt_json(encrypted)
    assert decrypted == cfg_payload


@pytest.mark.asyncio
async def test_healthcheck_survives_redis_down(async_client: AsyncClient, async_db_session, monkeypatch):
    _, token = await _make_admin(async_db_session)
    headers = {"Authorization": f"Bearer {token}"}

    # ensure config exists
    await ProviderConfigService.set_provider_config(
        async_db_session,
        domain="otp",
        provider="noop",
        config={"api_key": "a"},
    )

    def _raise(*args, **kwargs):
        raise RuntimeError("redis_down")

    monkeypatch.setattr(ProviderRegistry, "_redis_client", staticmethod(_raise))

    resp = await async_client.post(
        "/api/admin/integrations/providers/otp/noop/healthcheck",
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body.get("status") in {"ok", "error"}


@pytest.mark.asyncio
async def test_switch_provider_after_config_keeps_resolver(async_client: AsyncClient, async_db_session, monkeypatch):
    OtpProviderResolver.reset_cache()
    ProviderRegistry.invalidate()
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("DEBUG_PROVIDER_INFO", "1")

    _, token = await _make_admin(async_db_session)
    headers = {"Authorization": f"Bearer {token}"}

    for provider in ("noop-a", "noop-b"):
        resp = await async_client.post(
            "/api/admin/integrations/providers",
            headers=headers,
            json={"domain": "otp", "provider": provider, "config": {}},
        )
        assert resp.status_code == 201, resp.text
        cfg_resp = await async_client.put(
            f"/api/admin/integrations/providers/otp/{provider}/config",
            headers=headers,
            json={"config": {"label": provider}},
        )
        assert cfg_resp.status_code == 200, cfg_resp.text

    resp_activate_a = await async_client.post(
        "/api/admin/integrations/active",
        headers=headers,
        json={"domain": "otp", "provider": "noop-a"},
    )
    assert resp_activate_a.status_code == 200, resp_activate_a.text

    first = await async_client.post(
        "/api/v1/auth/request-otp",
        json={"phone": "+77000000222", "purpose": "login"},
    )
    assert first.status_code == 200, first.text
    first_provider = (first.json().get("data") or {}).get("provider")

    resp_activate_b = await async_client.post(
        "/api/admin/integrations/active",
        headers=headers,
        json={"domain": "otp", "provider": "noop-b"},
    )
    assert resp_activate_b.status_code == 200, resp_activate_b.text
    version_b = resp_activate_b.json().get("version")

    second = await async_client.post(
        "/api/v1/auth/request-otp",
        json={"phone": "+77000000223", "purpose": "login"},
    )
    assert second.status_code == 200, second.text
    data_second = second.json().get("data") or {}

    assert data_second.get("provider") == "noop-b"
    assert data_second.get("provider") != first_provider
    assert data_second.get("provider_version") == version_b
