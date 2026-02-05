from __future__ import annotations

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
