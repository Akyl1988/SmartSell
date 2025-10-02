import os

# Set testing environment variable
os.environ["TESTING"] = "1"

from app.core.config import get_settings


def test_get_settings():
    """Test settings configuration"""
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
