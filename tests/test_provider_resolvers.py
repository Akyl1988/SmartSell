from __future__ import annotations

import time

import pytest

from app.core.provider_registry import CachedProvider, ProviderRegistry
from app.integrations.providers.noop import NoOpMessagingProvider, NoOpPaymentGateway
from app.services.messaging_providers import MessagingProviderResolver
from app.services.payment_providers import PaymentProviderResolver


@pytest.fixture(autouse=True)
async def _reset_resolvers():
    MessagingProviderResolver.reset_cache()
    PaymentProviderResolver.reset_cache()
    yield
    MessagingProviderResolver.reset_cache()
    PaymentProviderResolver.reset_cache()


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
