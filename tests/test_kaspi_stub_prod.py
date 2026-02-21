from __future__ import annotations

import pytest
from fastapi import FastAPI

from app import main as main_mod
from app.core import config as config_mod


def _set_prod_secrets(monkeypatch) -> None:
    monkeypatch.setenv("SECRET_KEY", "x" * 32)
    monkeypatch.setenv("INVITE_TOKEN_SECRET", "y" * 32)
    monkeypatch.setenv("RESET_TOKEN_SECRET", "z" * 32)
    monkeypatch.setenv("OTP_SECRET", "o" * 32)


def test_kaspi_stub_enabled_in_prod_raises(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("SMARTSELL_KASPI_STUB", "1")
    _set_prod_secrets(monkeypatch)
    settings = config_mod.Settings()
    with pytest.raises(RuntimeError, match="KASPI_STUB must be disabled in production"):
        config_mod.validate_prod_secrets(settings)


def test_kaspi_stub_disabled_in_prod_allows(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("SMARTSELL_KASPI_STUB", "0")
    _set_prod_secrets(monkeypatch)
    settings = config_mod.Settings()
    config_mod.validate_prod_secrets(settings)


@pytest.mark.asyncio
async def test_startup_fails_with_stub_in_production(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("SMARTSELL_KASPI_STUB", "1")
    monkeypatch.setenv("DISABLE_APP_STARTUP_HOOKS", "1")
    _set_prod_secrets(monkeypatch)
    monkeypatch.setattr(main_mod, "settings", config_mod.Settings())

    app = FastAPI()
    with pytest.raises(RuntimeError, match="KASPI_STUB must be disabled in production"):
        async with main_mod.lifespan(app):
            pass
