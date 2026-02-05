from __future__ import annotations

import importlib

import pytest

from app.core import config as config_mod


def _reload_tokens():
    import app.utils.tokens as tokens

    return importlib.reload(tokens)


def _set_env(monkeypatch: pytest.MonkeyPatch, *, env: str, invite_secret: str | None, reset_secret: str | None):
    monkeypatch.setattr(config_mod.settings, "ENVIRONMENT", env)
    monkeypatch.setattr(config_mod.settings, "INVITE_TOKEN_SECRET", invite_secret)
    monkeypatch.setattr(config_mod.settings, "RESET_TOKEN_SECRET", reset_secret)


def test_invite_token_secret_required_in_prod(monkeypatch: pytest.MonkeyPatch):
    _set_env(monkeypatch, env="production", invite_secret=None, reset_secret="x" * 32)

    with pytest.raises(ValueError, match="invite_token_secret_required_in_prod"):
        config_mod.validate_prod_secrets(config_mod.settings)


def test_reset_token_secret_required_in_prod(monkeypatch: pytest.MonkeyPatch):
    _set_env(monkeypatch, env="production", invite_secret="x" * 32, reset_secret=None)

    with pytest.raises(ValueError, match="reset_token_secret_required_in_prod"):
        config_mod.validate_prod_secrets(config_mod.settings)


def test_non_prod_missing_token_secrets_allowed(monkeypatch: pytest.MonkeyPatch):
    _set_env(monkeypatch, env="development", invite_secret=None, reset_secret=None)
    config_mod.validate_prod_secrets(config_mod.settings)
    _reload_tokens()
