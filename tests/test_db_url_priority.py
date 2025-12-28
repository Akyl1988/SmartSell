import importlib

import pytest

from app.core import config


def _reset_settings_cache() -> None:
    config.get_settings.cache_clear()  # type: ignore[attr-defined]
    importlib.reload(config)


def test_pytest_requires_test_url(monkeypatch):
    env_url = "postgresql+asyncpg://postgres:envpass@host1:5432/db_env"

    # Ensure no test overrides
    for key in ("TEST_DATABASE_URL", "TEST_ASYNC_DATABASE_URL", "DATABASE_TEST_URL", "DB_URL"):
        monkeypatch.delenv(key, raising=False)

    # Provide temporary test URL so module reload (config.py) succeeds
    monkeypatch.setenv("TEST_DATABASE_URL", "postgresql://tmp:tmp@tmp:5432/tmp")
    monkeypatch.setenv("DATABASE_URL", env_url)

    _reset_settings_cache()
    try:
        # Now drop test URL and ensure get_settings fails under pytest without TEST_* DSN
        monkeypatch.delenv("TEST_DATABASE_URL", raising=False)
        config.get_settings.cache_clear()  # type: ignore[attr-defined]
        with pytest.raises(ValueError):
            config.get_settings()
    finally:
        # Restore a temporary test URL so config reload does not fail, then clear
        monkeypatch.setenv("TEST_DATABASE_URL", "postgresql://tmp:tmp@tmp:5432/tmp")
        _reset_settings_cache()
        monkeypatch.delenv("TEST_DATABASE_URL", raising=False)
        config.get_settings.cache_clear()  # type: ignore[attr-defined]


def test_test_database_url_wins(monkeypatch):
    env_url = "postgresql+asyncpg://postgres:envpass@host1:5432/db_env"
    test_url = "postgresql://postgres:testpass@host2:5432/db_test"

    for key in ("TEST_DATABASE_URL", "TEST_ASYNC_DATABASE_URL", "DATABASE_TEST_URL", "DB_URL"):
        monkeypatch.delenv(key, raising=False)

    monkeypatch.setenv("DATABASE_URL", env_url)
    monkeypatch.setenv("TEST_DATABASE_URL", test_url)

    _reset_settings_cache()
    try:
        s = config.get_settings()
        assert s.DATABASE_URL == test_url
        assert s.sqlalchemy_sync_url and "host2" in s.sqlalchemy_sync_url
        assert config.db_url_fingerprint(s.DATABASE_URL) == config.db_url_fingerprint(test_url)
    finally:
        _reset_settings_cache()
