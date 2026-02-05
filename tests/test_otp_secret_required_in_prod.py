from __future__ import annotations

import pytest

import app.core.config as config


def test_otp_secret_required_in_prod(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("SECRET_KEY", "x" * 48)
    monkeypatch.setenv("INVITE_TOKEN_SECRET", "x" * 48)
    monkeypatch.setenv("RESET_TOKEN_SECRET", "x" * 48)
    monkeypatch.delenv("OTP_SECRET", raising=False)

    config.get_settings.cache_clear()

    with pytest.raises(ValueError, match="otp_secret_required_in_prod"):
        config.validate_prod_secrets()
