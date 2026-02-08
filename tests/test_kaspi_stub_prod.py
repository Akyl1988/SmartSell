from __future__ import annotations

import pytest
from fastapi import FastAPI

from app import main as main_mod
from app.core import config as config_mod


def test_kaspi_stub_enabled_in_prod_raises(monkeypatch):
    monkeypatch.setenv("KASPI_STUB", "1")
    with pytest.raises(RuntimeError):
        config_mod._check_kaspi_stub_disabled()


def test_kaspi_stub_disabled_in_prod_allows(monkeypatch):
    monkeypatch.setenv("KASPI_STUB", "0")
    config_mod._check_kaspi_stub_disabled()


@pytest.mark.asyncio
async def test_startup_fails_with_stub_in_production(monkeypatch):
    monkeypatch.setenv("KASPI_STUB", "1")
    monkeypatch.setattr(main_mod, "validate_prod_secrets", lambda *args, **kwargs: None)
    monkeypatch.setattr(main_mod.settings, "ENVIRONMENT", "production")

    app = FastAPI()
    with pytest.raises(RuntimeError):
        async with main_mod.lifespan(app):
            pass
