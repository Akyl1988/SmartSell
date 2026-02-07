from __future__ import annotations

import sys

import pytest
from cryptography.fernet import Fernet

from app.core.config import settings
from app.core.provider_registry import ProviderRegistry
from app.integrations.errors import ProviderNotConfiguredError
from app.integrations.providers.cloudinary.media import CloudinaryMediaProvider
from app.services.integration_providers import IntegrationProviderService
from app.services.media_providers import MediaProviderResolver
from app.services.provider_configs import ProviderConfigService

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _setup_master_key(monkeypatch):
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("INTEGRATIONS_MASTER_KEY", key)
    yield


class _FakeUploader:
    def upload(self, *_args, **_kwargs):
        return {
            "public_id": "img-1",
            "secure_url": "https://cdn.example.com/img-1.jpg",
            "resource_type": "image",
        }

    def destroy(self, *_args, **_kwargs):
        return {"result": "ok"}


class _FakeCloudinary:
    def __init__(self):
        self.uploader = _FakeUploader()
        self._config = None

    def config(self, **kwargs):
        self._config = kwargs


async def test_cloudinary_media_upload_remove_happy_path(monkeypatch, async_db_session):
    ProviderRegistry.invalidate()
    MediaProviderResolver.reset_cache()
    monkeypatch.setenv("ENVIRONMENT", "development")

    fake_cloudinary = _FakeCloudinary()
    monkeypatch.setitem(sys.modules, "cloudinary", fake_cloudinary)

    await IntegrationProviderService.create_provider(
        async_db_session,
        domain="media",
        provider="cloudinary",
        config={},
        capabilities={},
        is_enabled=True,
        is_active=True,
    )
    await ProviderConfigService.set_provider_config(
        async_db_session,
        domain="media",
        provider="cloudinary",
        config={"cloud_name": "c1", "api_key": "k1", "api_secret": "s1"},
    )

    provider = await MediaProviderResolver.resolve(async_db_session, domain="media")
    upload_result = await provider.upload(b"img-bytes")
    assert upload_result.get("status") == "ok"
    assert upload_result.get("public_id") == "img-1"

    remove_result = await provider.remove("img-1")
    assert remove_result.get("status") == "ok"
    assert remove_result.get("result") == "ok"


@pytest.mark.asyncio
async def test_cloudinary_missing_config_in_prod(monkeypatch):
    monkeypatch.setattr(settings, "ENVIRONMENT", "production", raising=False)
    monkeypatch.setattr(settings, "CLOUDINARY_CLOUD_NAME", None, raising=False)
    monkeypatch.setattr(settings, "CLOUDINARY_API_KEY", None, raising=False)
    monkeypatch.setattr(settings, "CLOUDINARY_API_SECRET", None, raising=False)

    provider = CloudinaryMediaProvider(config={})
    with pytest.raises(ProviderNotConfiguredError, match="cloudinary_not_configured"):
        await provider.upload(b"img-bytes")
    with pytest.raises(ProviderNotConfiguredError, match="cloudinary_not_configured"):
        await provider.remove("img-1")
