from __future__ import annotations

import pytest

import app.core.config as config


def test_pgcrypto_key_required_in_prod(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("SECRET_KEY", "short-secret")
    monkeypatch.delenv("PGCRYPTO_KEY", raising=False)

    config.get_settings.cache_clear()
    settings = config.get_settings()

    with pytest.raises(ValueError, match="KASPI token encryption key is too short"):
        settings.get_kaspi_enc_key()
