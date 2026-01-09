from __future__ import annotations

from secrets import randbelow

import httpx
import pytest
from cryptography.fernet import Fernet
from httpx import Response

from app.core.provider_registry import ProviderRegistry
from app.core.security import create_access_token, get_password_hash
from app.integrations.providers.mobizon.otp import MobizonOtpProvider
from app.models.integration_provider import IntegrationProviderEvent
from app.models.user import User
from app.services.otp_providers import OtpProviderResolver
from app.services.provider_configs import ProviderConfigService


@pytest.fixture(autouse=True)
def _setup_master_key(monkeypatch):
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("INTEGRATIONS_MASTER_KEY", key)
    yield


async def _make_admin(async_db_session):
    suffix = str(randbelow(900000) + 100000)
    user = User(
        username=f"admin_mobizon_{suffix}",
        email=f"admin_mobizon_{suffix}@example.com",
        phone=f"+770000{suffix}",
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
async def test_send_otp_success(monkeypatch, async_client, async_db_session):
    OtpProviderResolver.reset_cache()
    ProviderRegistry.invalidate()
    _, token = await _make_admin(async_db_session)
    headers = {"Authorization": f"Bearer {token}"}
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("DEBUG_PROVIDER_INFO", "1")

    await async_client.post(
        "/api/admin/integrations/providers",
        headers=headers,
        json={
            "domain": "otp",
            "provider": "mobizon",
            "config": {},
            "is_enabled": True,
            "is_active": True,
        },
    )

    await async_client.put(
        "/api/admin/integrations/providers/otp/mobizon/config",
        headers=headers,
        json={"config": {"api_key": "k1", "base_url": "https://mobizon.test", "timeout_seconds": 1}},
    )

    async def fake_request(self, method, path, json=None, params=None, headers=None):
        return Response(200, json={"data": {"messageId": "m-1"}})

    monkeypatch.setattr(MobizonOtpProvider, "_request", fake_request)

    resp = await async_client.post(
        "/api/v1/auth/request-otp",
        json={"phone": "+77000001010", "purpose": "login"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json().get("data") or {}
    assert data.get("provider") == "mobizon"
    assert data.get("provider_success") is True


@pytest.mark.asyncio
async def test_send_otp_failure(monkeypatch, async_client, async_db_session):
    OtpProviderResolver.reset_cache()
    ProviderRegistry.invalidate()
    _, token = await _make_admin(async_db_session)
    headers = {"Authorization": f"Bearer {token}"}
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("DEBUG_PROVIDER_INFO", "1")

    await async_client.post(
        "/api/admin/integrations/providers",
        headers=headers,
        json={
            "domain": "otp",
            "provider": "mobizon",
            "config": {},
            "is_enabled": True,
            "is_active": True,
        },
    )

    await async_client.put(
        "/api/admin/integrations/providers/otp/mobizon/config",
        headers=headers,
        json={"config": {"api_key": "k1", "base_url": "https://mobizon.test", "timeout_seconds": 1}},
    )

    async def fake_request(self, method, path, json=None, params=None, headers=None):
        return Response(500, json={"error": "fail"})

    monkeypatch.setattr(MobizonOtpProvider, "_request", fake_request)

    resp = await async_client.post(
        "/api/v1/auth/request-otp",
        json={"phone": "+77000001011", "purpose": "login"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json().get("data") or {}
    assert data.get("provider") == "mobizon"
    assert data.get("provider_success") is None
    assert data.get("provider_status") == "error"


@pytest.mark.asyncio
async def test_missing_config_falls_back_and_records_event(monkeypatch, async_client, async_db_session):
    OtpProviderResolver.reset_cache()
    ProviderRegistry.invalidate()
    _, token = await _make_admin(async_db_session)
    headers = {"Authorization": f"Bearer {token}"}
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("DEBUG_PROVIDER_INFO", "1")

    await async_client.post(
        "/api/admin/integrations/providers",
        headers=headers,
        json={
            "domain": "otp",
            "provider": "mobizon",
            "config": {},
            "is_enabled": True,
            "is_active": True,
        },
    )

    resp = await async_client.post(
        "/api/v1/auth/request-otp",
        json={"phone": "+77000001012", "purpose": "login"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json().get("data") or {}
    assert data.get("provider") == "noop"

    res = await async_db_session.execute(IntegrationProviderEvent.__table__.select())
    events = res.fetchall()
    assert any((row.meta_json or {}).get("action") == "config_missing" for row in events)


@pytest.mark.asyncio
async def test_resolver_uses_new_config_after_update(monkeypatch, async_client, async_db_session):
    OtpProviderResolver.reset_cache()
    ProviderRegistry.invalidate()
    _, token = await _make_admin(async_db_session)
    headers = {"Authorization": f"Bearer {token}"}
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("DEBUG_PROVIDER_INFO", "1")

    await async_client.post(
        "/api/admin/integrations/providers",
        headers=headers,
        json={
            "domain": "otp",
            "provider": "noop",
            "config": {},
            "is_enabled": True,
            "is_active": True,
        },
    )
    await async_client.post(
        "/api/admin/integrations/providers",
        headers=headers,
        json={
            "domain": "otp",
            "provider": "mobizon",
            "config": {},
            "is_enabled": True,
            "is_active": False,
        },
    )

    await async_client.put(
        "/api/admin/integrations/providers/otp/mobizon/config",
        headers=headers,
        json={"config": {"api_key": "k1", "base_url": "https://mobizon.test", "timeout_seconds": 1}},
    )

    async def fake_request(self, method, path, json=None, params=None, headers=None):
        return Response(200, json={"data": {"messageId": "m-2"}})

    monkeypatch.setattr(MobizonOtpProvider, "_request", fake_request)

    # activate mobizon
    await async_client.post(
        "/api/admin/integrations/active",
        headers=headers,
        json={"domain": "otp", "provider": "mobizon"},
    )

    resp = await async_client.post(
        "/api/v1/auth/request-otp",
        json={"phone": "+77000001013", "purpose": "login"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json().get("data") or {}
    assert data.get("provider") == "mobizon"


@pytest.mark.asyncio
async def test_redis_down_does_not_break_send(monkeypatch, async_client, async_db_session):
    OtpProviderResolver.reset_cache()
    ProviderRegistry.invalidate()
    _, token = await _make_admin(async_db_session)
    headers = {"Authorization": f"Bearer {token}"}
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("DEBUG_PROVIDER_INFO", "1")

    await async_client.post(
        "/api/admin/integrations/providers",
        headers=headers,
        json={
            "domain": "otp",
            "provider": "mobizon",
            "config": {},
            "is_enabled": True,
            "is_active": True,
        },
    )

    await async_client.put(
        "/api/admin/integrations/providers/otp/mobizon/config",
        headers=headers,
        json={"config": {"api_key": "k1", "base_url": "https://mobizon.test", "timeout_seconds": 1}},
    )

    async def fake_request(self, method, path, json=None, params=None, headers=None):
        return Response(200, json={"data": {"messageId": "m-3"}})

    monkeypatch.setattr(MobizonOtpProvider, "_request", fake_request)

    def _raise(*args, **kwargs):
        raise RuntimeError("redis_down")

    monkeypatch.setattr(ProviderRegistry, "_redis_client", staticmethod(_raise))

    resp = await async_client.post(
        "/api/v1/auth/request-otp",
        json={"phone": "+77000001014", "purpose": "login"},
    )
    assert resp.status_code == 200, resp.text


@pytest.mark.asyncio
async def test_verify_otp_direct(monkeypatch):
    provider = MobizonOtpProvider(config={"api_key": "k1", "base_url": "https://mobizon.test"})

    async def fake_request(self, method, path, json=None, params=None, headers=None):
        return Response(200, json={"data": {"verified": True}})

    monkeypatch.setattr(MobizonOtpProvider, "_request", fake_request)

    res = await provider.verify_otp(phone="+77000000000", code="123456")
    assert res.get("verified") is True
    assert res.get("status") == "ok"
