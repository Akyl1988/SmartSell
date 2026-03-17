from __future__ import annotations

import importlib
import sys

import pytest

import app.core.config as config_mod
import app.core.security as security


def test_denylist_backend_required_in_prod(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DENYLIST_REQUIRE_BACKEND", "1")
    monkeypatch.setattr(config_mod.settings, "ENVIRONMENT", "production", raising=False)
    monkeypatch.setattr(security.settings, "ENVIRONMENT", "production", raising=False)
    monkeypatch.setattr(security, "_HAS_SQLA", False, raising=False)
    monkeypatch.setattr(security, "_SYNC_ENGINE_AVAILABLE", False, raising=False)
    monkeypatch.setattr(security, "_HAS_REDIS", False, raising=False)

    with pytest.raises(RuntimeError, match="denylist_backend_required_in_prod"):
        security._build_denylist_backend()


def test_security_import_does_not_eager_init_denylist(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DENYLIST_REQUIRE_BACKEND", "1")
    monkeypatch.setattr(config_mod.settings, "ENVIRONMENT", "production", raising=False)

    mod = importlib.import_module("app.core.security")
    assert mod._DENYLIST is None


def test_router_imports_succeed_when_denylist_backend_unavailable(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DENYLIST_REQUIRE_BACKEND", "1")
    monkeypatch.setattr(config_mod.settings, "ENVIRONMENT", "production", raising=False)
    monkeypatch.setattr(security.settings, "ENVIRONMENT", "production", raising=False)
    monkeypatch.setattr(security, "_HAS_SQLA", False, raising=False)
    monkeypatch.setattr(security, "_SYNC_ENGINE_AVAILABLE", False, raising=False)
    monkeypatch.setattr(security, "_HAS_REDIS", False, raising=False)
    monkeypatch.setattr(security, "_DENYLIST", None, raising=False)

    modules = [
        "app.api.v1.auth",
        "app.api.v1.users",
        "app.api.v1.products",
        "app.api.v1.campaigns",
        "app.api.v1.wallet",
        "app.api.v1.payments",
    ]

    for name in modules:
        sys.modules.pop(name, None)
        imported = importlib.import_module(name)
        assert imported is not None


def test_denylist_operation_still_fails_in_prod_when_backend_required(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DENYLIST_REQUIRE_BACKEND", "1")
    monkeypatch.setattr(config_mod.settings, "ENVIRONMENT", "production", raising=False)
    monkeypatch.setattr(security.settings, "ENVIRONMENT", "production", raising=False)
    monkeypatch.setattr(security, "_HAS_SQLA", False, raising=False)
    monkeypatch.setattr(security, "_SYNC_ENGINE_AVAILABLE", False, raising=False)
    monkeypatch.setattr(security, "_HAS_REDIS", False, raising=False)
    monkeypatch.setattr(security, "_DENYLIST", None, raising=False)

    with pytest.raises(RuntimeError, match="denylist_backend_required_in_prod"):
        security.revoke_token("prod-jti")


def test_non_prod_denylist_fallback_still_works(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("DENYLIST_REQUIRE_BACKEND", raising=False)
    monkeypatch.setattr(config_mod.settings, "ENVIRONMENT", "development", raising=False)
    monkeypatch.setattr(security.settings, "ENVIRONMENT", "development", raising=False)
    monkeypatch.setattr(security, "_HAS_SQLA", False, raising=False)
    monkeypatch.setattr(security, "_SYNC_ENGINE_AVAILABLE", False, raising=False)
    monkeypatch.setattr(security, "_HAS_REDIS", False, raising=False)
    monkeypatch.setattr(security, "_DENYLIST", None, raising=False)

    security.revoke_token("dev-jti", ttl_seconds=60)
    assert security.is_token_revoked("dev-jti") is True
