from __future__ import annotations

import time

import pytest
from cryptography.fernet import Fernet

from app.core.provider_registry import CachedProvider, ProviderRegistry
from app.core.security import create_access_token, get_password_hash
from app.models.user import User
from app.services.payment_providers import PaymentProviderResolver


@pytest.fixture(autouse=True)
def _setup_master_key(monkeypatch):
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("INTEGRATIONS_MASTER_KEY", key)
    yield


@pytest.fixture(autouse=True)
async def _reset_payment_resolver():
    PaymentProviderResolver.reset_cache()
    yield
    PaymentProviderResolver.reset_cache()


async def _make_admin(async_db_session):
    user = User(
        username="admin_payments",
        email="admin_payments@example.com",
        phone="+77000000401",
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
async def test_payments_provider_hot_switch(monkeypatch):
    call_count = {"n": 0}

    async def fake_get_active(db, domain):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return CachedProvider(provider="noop-pay-a", config={"a": 1}, version=1, cached_at=time.monotonic())
        return CachedProvider(provider="noop-pay-b", config={"b": 2}, version=2, cached_at=time.monotonic())

    monkeypatch.setattr(ProviderRegistry, "get_active_provider", staticmethod(fake_get_active))

    first = await PaymentProviderResolver.resolve(None, domain="payments")
    second = await PaymentProviderResolver.resolve(None, domain="payments")

    assert first is not second
    assert getattr(first, "provider_name", None) == "noop-pay-a"
    assert getattr(second, "provider_name", None) == "noop-pay-b"
    assert getattr(second, "provider_version", None) == 2


@pytest.mark.asyncio
async def test_payments_config_redaction_no_secrets(monkeypatch, async_client, async_db_session):
    _, token = await _make_admin(async_db_session)
    headers = {"Authorization": f"Bearer {token}"}
    monkeypatch.setenv("ENVIRONMENT", "development")

    await async_client.post(
        "/api/admin/integrations/providers",
        headers=headers,
        json={
            "domain": "payments",
            "provider": "noop-pay",
            "config": {},
            "is_enabled": True,
            "is_active": True,
        },
    )

    await async_client.put(
        "/api/admin/integrations/payments/config",
        headers=headers,
        json={"provider": "noop-pay", "config": {"api_key": "secret", "nested": {"token": "abc"}}},
    )

    resp = await async_client.get(
        "/api/admin/integrations/payments/config",
        headers=headers,
        params={"provider": "noop-pay"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["config"]["api_key"] == "***"
    assert data["config"]["nested"]["token"] == "***"


@pytest.mark.asyncio
async def test_payments_healthcheck_redis_down_resilient(monkeypatch, async_client, async_db_session):
    _, token = await _make_admin(async_db_session)
    headers = {"Authorization": f"Bearer {token}"}

    def _raise(*args, **kwargs):  # pragma: no cover - defensive
        raise RuntimeError("redis_down")

    monkeypatch.setattr(ProviderRegistry, "_redis_client", staticmethod(_raise))

    await async_client.post(
        "/api/admin/integrations/providers",
        headers=headers,
        json={
            "domain": "payments",
            "provider": "noop-pay",
            "config": {},
            "is_enabled": True,
            "is_active": True,
        },
    )

    await async_client.put(
        "/api/admin/integrations/payments/config",
        headers=headers,
        json={"provider": "noop-pay", "config": {"api_key": "secret"}},
    )

    resp = await async_client.get(
        "/api/admin/integrations/payments/healthcheck",
        headers=headers,
        params={"provider": "noop-pay"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["status"] in {"ok", "error"}
    # Should not crash even if redis client fails
    assert data["domain"] == "payments"
    assert data["provider"] == "noop-pay"
