from __future__ import annotations

import time

import pytest

from app.core.config import settings
from app.core.provider_registry import CachedProvider, ProviderRegistry
from app.integrations.providers.mobizon.otp import MobizonOtpProvider
from app.integrations.providers.noop import NoOpMessagingProvider, NoOpPaymentGateway
from app.integrations.providers.smtp.messaging import SmtpMessagingProvider
from app.services.messaging_providers import MessagingProviderResolver
from app.services.otp_providers import OtpProviderResolver
from app.services.payment_providers import PaymentProviderResolver
from app.services.provider_configs import ProviderConfigService


@pytest.fixture(autouse=True)
async def _reset_resolvers():
    MessagingProviderResolver.reset_cache()
    PaymentProviderResolver.reset_cache()
    OtpProviderResolver.reset_cache()
    yield
    MessagingProviderResolver.reset_cache()
    PaymentProviderResolver.reset_cache()
    OtpProviderResolver.reset_cache()


@pytest.mark.asyncio
async def test_messaging_provider_hot_switch(monkeypatch):
    call_count = {"n": 0}

    async def fake_get_active(db, domain):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return CachedProvider(
                provider="noop-a",
                config={"label": "a"},
                version=1,
                cached_at=time.monotonic(),
            )
        return CachedProvider(
            provider="noop-b",
            config={"label": "b"},
            version=2,
            cached_at=time.monotonic(),
        )

    monkeypatch.setattr(ProviderRegistry, "get_active_provider", staticmethod(fake_get_active))

    first = await MessagingProviderResolver.resolve(None, domain="messaging")
    second = await MessagingProviderResolver.resolve(None, domain="messaging")

    assert first is not second
    assert getattr(first, "name", None) == "noop-a"
    assert getattr(second, "name", None) == "noop-b"
    assert getattr(second, "version", None) == 2


@pytest.mark.asyncio
async def test_messaging_provider_fallback(monkeypatch):
    async def failing(db, domain):  # pragma: no cover - exercised by test
        raise RuntimeError("boom")

    monkeypatch.setattr(ProviderRegistry, "get_active_provider", staticmethod(failing))

    provider = await MessagingProviderResolver.resolve(None, domain="messaging")
    assert isinstance(provider, NoOpMessagingProvider)
    assert provider.name == "noop"


@pytest.mark.asyncio
async def test_messaging_provider_noop_allowed_in_dev(monkeypatch):
    monkeypatch.setattr(settings, "ENVIRONMENT", "development", raising=False)

    async def no_provider(db, domain):
        return None

    monkeypatch.setattr(ProviderRegistry, "get_active_provider", staticmethod(no_provider))

    provider = await MessagingProviderResolver.resolve(None, domain="messaging")
    assert isinstance(provider, NoOpMessagingProvider)


@pytest.mark.asyncio
async def test_otp_provider_mobizon_resolution(monkeypatch):
    monkeypatch.setattr(settings, "ENVIRONMENT", "development", raising=False)
    mobizon_config = {"api_key": "key", "sender": "SENDER"}

    async def fake_get_active(db, domain):
        return CachedProvider(
            provider="mobizon",
            config=mobizon_config,
            version=1,
            cached_at=time.monotonic(),
        )

    async def fake_get_config(*_args, **_kwargs):
        return mobizon_config

    monkeypatch.setattr(ProviderRegistry, "get_active_provider", staticmethod(fake_get_active))
    monkeypatch.setattr(ProviderConfigService, "get_provider_config", staticmethod(fake_get_config))

    provider = await OtpProviderResolver.resolve(None, domain="otp")
    assert isinstance(provider, MobizonOtpProvider)
    assert provider.api_key == "key"


@pytest.mark.asyncio
async def test_messaging_provider_smtp_resolution(monkeypatch):
    monkeypatch.setattr(settings, "ENVIRONMENT", "development", raising=False)
    smtp_config = {
        "host": "smtp.example.com",
        "port": 587,
        "user": "user",
        "password": "pass",
        "from_email": "noreply@example.com",
        "tls": True,
    }

    async def fake_get_active(db, domain):
        return CachedProvider(
            provider="smtp",
            config=smtp_config,
            version=1,
            cached_at=time.monotonic(),
        )

    monkeypatch.setattr(ProviderRegistry, "get_active_provider", staticmethod(fake_get_active))

    async def fake_get_config(*_args, **_kwargs):
        return smtp_config

    monkeypatch.setattr(ProviderConfigService, "get_provider_config", staticmethod(fake_get_config))

    provider = await MessagingProviderResolver.resolve(None, domain="messaging")
    assert isinstance(provider, SmtpMessagingProvider)
    assert provider.host == "smtp.example.com"


@pytest.mark.asyncio
async def test_payment_provider_hot_switch(monkeypatch):
    call_count = {"n": 0}

    async def fake_get_active(db, domain):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return CachedProvider(
                provider="noop-pay-a",
                config={"merchant": "a"},
                version=3,
                cached_at=time.monotonic(),
            )
        return CachedProvider(
            provider="noop-pay-b",
            config={"merchant": "b"},
            version=4,
            cached_at=time.monotonic(),
        )

    monkeypatch.setattr(ProviderRegistry, "get_active_provider", staticmethod(fake_get_active))

    first = await PaymentProviderResolver.resolve(None, domain="payments")
    second = await PaymentProviderResolver.resolve(None, domain="payments")

    assert first is not second
    assert getattr(first, "name", None) == "noop-pay-a"
    assert getattr(second, "name", None) == "noop-pay-b"
    assert getattr(second, "version", None) == 4


@pytest.mark.asyncio
async def test_payment_provider_fallback(monkeypatch):
    async def failing(db, domain):  # pragma: no cover - exercised by test
        raise RuntimeError("boom")

    monkeypatch.setattr(ProviderRegistry, "get_active_provider", staticmethod(failing))

    provider = await PaymentProviderResolver.resolve(None, domain="payments")
    assert isinstance(provider, NoOpPaymentGateway)
    assert provider.name == "noop"
