import importlib

import pytest

from app.core import config


def _reset_settings_cache() -> None:
    config.get_settings.cache_clear()  # type: ignore[attr-defined]
    importlib.reload(config)


def test_testing_prefers_test_database_url(monkeypatch):
    test_url = "postgresql://postgres:testpass@host2:5432/db_test"
    env_url = "postgresql://postgres:envpass@host1:5432/db_env"

    for key in ("TEST_DATABASE_URL", "TEST_ASYNC_DATABASE_URL", "DATABASE_TEST_URL", "DB_URL", "DATABASE_URL"):
        monkeypatch.delenv(key, raising=False)

    monkeypatch.setenv("TESTING", "1")
    monkeypatch.setenv("TEST_DATABASE_URL", test_url)
    monkeypatch.setenv("DATABASE_URL", env_url)

    _reset_settings_cache()
    s = config.get_settings()
    assert s.DATABASE_URL == test_url
    assert getattr(s, "DB_URL_SOURCE", "") == "TEST_DATABASE_URL"


def test_testing_uses_database_url_when_no_test(monkeypatch):
    env_url = "postgresql://postgres:envpass@host1:5432/db_env"

    for key in ("TEST_DATABASE_URL", "TEST_ASYNC_DATABASE_URL", "DATABASE_TEST_URL", "DB_URL"):
        monkeypatch.delenv(key, raising=False)

    monkeypatch.setenv("TESTING", "1")
    monkeypatch.setenv("DATABASE_URL", env_url)

    _reset_settings_cache()
    s = config.get_settings()
    assert s.DATABASE_URL == env_url
    assert getattr(s, "DB_URL_SOURCE", "") == "DATABASE_URL"


def test_database_url_used_when_not_testing(monkeypatch):
    env_url = "postgresql://postgres:envpass@host1:5432/db_env"

    for key in ("TESTING", "TEST_DATABASE_URL", "TEST_ASYNC_DATABASE_URL", "DATABASE_TEST_URL", "DB_URL"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("DATABASE_URL", env_url)

    _reset_settings_cache()
    s = config.get_settings()
    assert s.DATABASE_URL == env_url
    assert getattr(s, "DB_URL_SOURCE", "") == "DATABASE_URL"


def test_fallback_allowed_in_local_env(monkeypatch):
    for key in (
        "TESTING",
        "TEST_DATABASE_URL",
        "TEST_ASYNC_DATABASE_URL",
        "DATABASE_TEST_URL",
        "DB_URL",
        "DATABASE_URL",
        "TEST_DB_USER",
        "TEST_DB_PASSWORD",
        "TEST_DB_HOST",
        "TEST_DB_PORT",
        "TEST_DB_NAME",
        "POSTGRES_USER",
        "POSTGRES_PASSWORD",
        "POSTGRES_HOST",
        "POSTGRES_PORT",
        "POSTGRES_DB",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.setenv("TEST_DATABASE_URL", "")
    monkeypatch.setenv("ENVIRONMENT", "local")

    _reset_settings_cache()
    s = config.get_settings()
    assert getattr(s, "DB_URL_SOURCE", "") == "DEFAULT"
    assert s.DATABASE_URL == "sqlite+aiosqlite:///./.smartsell_test.sqlite3"


def test_no_fallback_outside_local(monkeypatch):
    for key in ("TESTING", "TEST_DATABASE_URL", "TEST_ASYNC_DATABASE_URL", "DATABASE_TEST_URL", "DB_URL", "DATABASE_URL"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.setenv("TEST_DATABASE_URL", "")
    monkeypatch.setenv("ENVIRONMENT", "production")

    with pytest.raises(ValueError):
        config.resolve_database_url(config.Settings())


def test_non_testing_prefers_database_url(monkeypatch):
    env_url = "postgresql://postgres:envpass@host1:5432/db_env"
    test_url = "postgresql://postgres:testpass@host2:5432/db_test"

    for key in ("TESTING", "TEST_DATABASE_URL", "TEST_ASYNC_DATABASE_URL", "DATABASE_TEST_URL", "DB_URL"):
        monkeypatch.delenv(key, raising=False)

    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("TESTING", "0")
    monkeypatch.setenv("DATABASE_URL", env_url)
    monkeypatch.setenv("TEST_DATABASE_URL", test_url)

    config.get_settings.cache_clear()  # type: ignore[attr-defined]
    monkeypatch.setattr(config, "_under_pytest", lambda: False)
    s = config.get_settings()

    assert s.DATABASE_URL == env_url
    assert getattr(s, "DB_URL_SOURCE", "") == "DATABASE_URL"
    assert getattr(s, "DB_URL_FINGERPRINT", "") == config.db_url_fingerprint(env_url)
