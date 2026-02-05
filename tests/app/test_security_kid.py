from __future__ import annotations

import importlib

import pytest

import app.core.config as config_mod
import app.core.security as security


def test_kid_rotation_requires_key_material_in_prod(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("JWT_ACTIVE_KID", "kid1")
    monkeypatch.delenv("JWT_KEYS_kid1_PRIVATE", raising=False)
    monkeypatch.delenv("JWT_KEYS_kid1_PUBLIC", raising=False)
    monkeypatch.delenv("JWT_KEYS_kid1_PRIVATE_PATH", raising=False)
    monkeypatch.delenv("JWT_KEYS_kid1_PUBLIC_PATH", raising=False)

    monkeypatch.setattr(config_mod.settings, "ENVIRONMENT", "production", raising=False)

    importlib.reload(security)
    monkeypatch.setattr(security.settings, "ENVIRONMENT", "production", raising=False)

    with pytest.raises(RuntimeError, match="kid_key_material_required_in_prod"):
        security.create_access_token("1")
