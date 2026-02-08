import pytest

import app.core.config as config
import app.main as main_module
from app.main import create_app


@pytest.mark.asyncio
async def test_startup_fails_on_insecure_secret_in_prod(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("DEBUG", "0")
    monkeypatch.setenv("SECRET_KEY", "changeme")
    monkeypatch.setenv("DISABLE_APP_STARTUP_HOOKS", "1")

    config.get_settings.cache_clear()
    new_settings = config.get_settings()
    monkeypatch.setattr(config, "settings", new_settings, raising=False)
    monkeypatch.setattr(main_module, "settings", new_settings, raising=False)
    monkeypatch.setattr(config.settings, "ENVIRONMENT", "production", raising=False)
    monkeypatch.setattr(config.settings, "DEBUG", False, raising=False)
    monkeypatch.setattr(config.settings, "SECRET_KEY", "changeme", raising=False)

    app = create_app()

    with pytest.raises(ValueError):
        async with app.router.lifespan_context(app):
            pass


@pytest.mark.asyncio
async def test_startup_allows_secure_secret_in_prod(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("DEBUG", "0")
    monkeypatch.setenv("SECRET_KEY", "x" * 48)
    monkeypatch.setenv("INVITE_TOKEN_SECRET", "x" * 48)
    monkeypatch.setenv("RESET_TOKEN_SECRET", "x" * 48)
    monkeypatch.setenv("OTP_SECRET", "x" * 48)
    monkeypatch.setenv("KASPI_STUB", "0")
    monkeypatch.setenv("DISABLE_APP_STARTUP_HOOKS", "1")

    config.get_settings.cache_clear()
    new_settings = config.get_settings()
    monkeypatch.setattr(config, "settings", new_settings, raising=False)
    monkeypatch.setattr(main_module, "settings", new_settings, raising=False)
    monkeypatch.setattr(config.settings, "ENVIRONMENT", "production", raising=False)
    monkeypatch.setattr(config.settings, "DEBUG", False, raising=False)
    monkeypatch.setattr(config.settings, "SECRET_KEY", "x" * 48, raising=False)
    monkeypatch.setattr(config.settings, "INVITE_TOKEN_SECRET", "x" * 48, raising=False)
    monkeypatch.setattr(config.settings, "RESET_TOKEN_SECRET", "x" * 48, raising=False)

    app = create_app()

    async with app.router.lifespan_context(app):
        pass


@pytest.mark.asyncio
async def test_startup_fails_when_mobizon_missing_in_prod(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("DEBUG", "0")
    monkeypatch.setenv("SECRET_KEY", "x" * 48)
    monkeypatch.setenv("INVITE_TOKEN_SECRET", "x" * 48)
    monkeypatch.setenv("RESET_TOKEN_SECRET", "x" * 48)
    monkeypatch.setenv("OTP_SECRET", "x" * 48)
    monkeypatch.setenv("STARTUP_REQUIRE_PROVIDERS", "1")
    monkeypatch.setenv("DISABLE_APP_STARTUP_HOOKS", "1")

    config.get_settings.cache_clear()
    new_settings = config.get_settings()
    monkeypatch.setattr(config, "settings", new_settings, raising=False)
    monkeypatch.setattr(main_module, "settings", new_settings, raising=False)
    monkeypatch.setattr(config.settings, "ENVIRONMENT", "production", raising=False)
    monkeypatch.setattr(config.settings, "DEBUG", False, raising=False)
    monkeypatch.setattr(config.settings, "SECRET_KEY", "x" * 48, raising=False)
    monkeypatch.setattr(config.settings, "INVITE_TOKEN_SECRET", "x" * 48, raising=False)
    monkeypatch.setattr(config.settings, "RESET_TOKEN_SECRET", "x" * 48, raising=False)

    async def _provider_check():
        return {"otp": {"ok": False, "detail": "mobizon_missing_config"}}

    monkeypatch.setattr(main_module, "_check_provider_registry", _provider_check)

    app = create_app()

    with pytest.raises(RuntimeError, match="otp_provider_not_configured"):
        async with app.router.lifespan_context(app):
            pass


@pytest.mark.asyncio
async def test_startup_fails_when_payments_noop_in_prod(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("DEBUG", "0")
    monkeypatch.setenv("SECRET_KEY", "x" * 48)
    monkeypatch.setenv("INVITE_TOKEN_SECRET", "x" * 48)
    monkeypatch.setenv("RESET_TOKEN_SECRET", "x" * 48)
    monkeypatch.setenv("OTP_SECRET", "x" * 48)
    monkeypatch.setenv("STARTUP_REQUIRE_PROVIDERS", "1")
    monkeypatch.setenv("DISABLE_APP_STARTUP_HOOKS", "1")

    config.get_settings.cache_clear()
    new_settings = config.get_settings()
    monkeypatch.setattr(config, "settings", new_settings, raising=False)
    monkeypatch.setattr(main_module, "settings", new_settings, raising=False)
    monkeypatch.setattr(config.settings, "ENVIRONMENT", "production", raising=False)
    monkeypatch.setattr(config.settings, "DEBUG", False, raising=False)
    monkeypatch.setattr(config.settings, "SECRET_KEY", "x" * 48, raising=False)
    monkeypatch.setattr(config.settings, "INVITE_TOKEN_SECRET", "x" * 48, raising=False)
    monkeypatch.setattr(config.settings, "RESET_TOKEN_SECRET", "x" * 48, raising=False)

    async def _provider_check():
        return {"payments": {"ok": False, "detail": "provider_noop"}}

    monkeypatch.setattr(main_module, "_check_provider_registry", _provider_check)

    app = create_app()

    with pytest.raises(RuntimeError, match="payment_provider_not_configured"):
        async with app.router.lifespan_context(app):
            pass
