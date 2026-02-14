from __future__ import annotations

import importlib

import pytest


def _reload_config():
    import app.core.config as config_mod

    return importlib.reload(config_mod)


def test_startup_fails_on_missing_secret_in_prod(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.delenv("SECRET_KEY", raising=False)

    config_mod = _reload_config()
    config_mod.get_settings.cache_clear()

    with pytest.raises(ValueError, match="SECRET_KEY"):
        config_mod.validate_prod_secrets(config_mod.get_settings())


def test_startup_fails_on_insecure_secret_in_prod(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("SECRET_KEY", "changeme")

    config_mod = _reload_config()
    config_mod.get_settings.cache_clear()

    with pytest.raises(ValueError, match="SECRET_KEY"):
        config_mod.validate_prod_secrets(config_mod.get_settings())
