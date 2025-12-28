import os

# Set testing environment variable
os.environ["TESTING"] = "1"

from app.core.config import get_settings


def test_get_settings():
    """Test settings configuration"""
    try:
        get_settings.cache_clear()
    except Exception:
        pass
    settings = get_settings()

    # Test values relevant for your environment
    assert settings.DATABASE_URL.startswith("postgresql")
    assert settings.API_V1_STR == "/api/v1"
    assert settings.PROJECT_NAME == "SmartSell"
    assert settings.SCHEDULER_TIMEZONE == "UTC"
    assert settings.SMTP_PORT == 587


def test_settings_singleton():
    """Test that get_settings returns the same instance"""
    settings1 = get_settings()
    settings2 = get_settings()

    assert settings1 is settings2


def test_smtp_port_forced_secure_in_testing(monkeypatch):
    """Testing/CI must not inherit insecure SMTP_PORT overrides from .env."""
    try:
        get_settings.cache_clear()
    except Exception:
        pass

    monkeypatch.setenv("TESTING", "1")
    monkeypatch.setenv("SMTP_PORT", "25")
    monkeypatch.delenv("SMTP_PORT_TEST", raising=False)

    settings = get_settings()
    assert settings.SMTP_PORT == 587

    try:
        get_settings.cache_clear()
    except Exception:
        pass
