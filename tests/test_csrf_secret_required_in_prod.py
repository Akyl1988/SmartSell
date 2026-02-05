from __future__ import annotations

import importlib
import os

import pytest

from app.core import config as config_mod


def _reload_security():
    import app.core.security as security

    return importlib.reload(security)


def _set_env(monkeypatch: pytest.MonkeyPatch, *, env: str, secret_key: str, csrf_secret: str | None) -> None:
    monkeypatch.setattr(config_mod.settings, "ENVIRONMENT", env)
    monkeypatch.setattr(config_mod.settings, "SECRET_KEY", secret_key)
    if csrf_secret is None:
        monkeypatch.delenv("CSRF_SECRET", raising=False)
    else:
        monkeypatch.setenv("CSRF_SECRET", csrf_secret)


def test_csrf_secret_required_in_prod_missing(monkeypatch: pytest.MonkeyPatch):
    _set_env(monkeypatch, env="production", secret_key="secret-key-123", csrf_secret=None)

    with pytest.raises(RuntimeError, match="csrf_secret_required_in_prod"):
        _reload_security()

    _set_env(monkeypatch, env="development", secret_key="secret-key-123", csrf_secret=None)
    _reload_security()


def test_csrf_secret_required_in_prod_equal(monkeypatch: pytest.MonkeyPatch):
    _set_env(monkeypatch, env="production", secret_key="secret-key-123", csrf_secret="secret-key-123")

    with pytest.raises(RuntimeError, match="csrf_secret_must_differ_from_secret_key"):
        _reload_security()

    _set_env(monkeypatch, env="development", secret_key="secret-key-123", csrf_secret=None)
    _reload_security()


def test_csrf_secret_not_required_non_prod(monkeypatch: pytest.MonkeyPatch):
    _set_env(monkeypatch, env="development", secret_key="secret-key-123", csrf_secret=None)
    _reload_security()
