import os
from urllib.parse import urlparse

from app.core.config import Settings, resolve_async_database_url


def _clear_all_db_env(monkeypatch):
    for key in (
        "PYTEST_CURRENT_TEST",
        "TESTING",
        "ENVIRONMENT",
        "TEST_ASYNC_DATABASE_URL",
        "TEST_DATABASE_URL",
        "DATABASE_TEST_URL",
        "DATABASE_URL",
        "DB_URL",
        "DB_USER",
        "DB_PASSWORD",
        "DB_HOST",
        "DB_PORT",
        "DB_NAME",
        "TEST_DB_USER",
        "TEST_DB_PASSWORD",
        "TEST_DB_HOST",
        "TEST_DB_PORT",
        "TEST_DB_NAME",
    ):
        monkeypatch.delenv(key, raising=False)


def test_runtime_prefers_database_url_over_test(monkeypatch):
    _clear_all_db_env(monkeypatch)

    # No pytest/test flags
    monkeypatch.setenv("ENVIRONMENT", "development")

    # Both DATABASE_URL and TEST_* are present
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg2://mainuser:mainpw@127.0.0.1:5432/smartsell")
    monkeypatch.setenv("TEST_ASYNC_DATABASE_URL", "postgresql+asyncpg://postgres:test@127.0.0.1:5432/smartsell_test")
    monkeypatch.setenv("TEST_DATABASE_URL", "postgresql+psycopg2://postgres:test@127.0.0.1:5432/smartsell_test")

    url, source, _ = resolve_async_database_url(Settings())
    parsed = urlparse(url)

    assert parsed.scheme.startswith("postgresql+asyncpg")
    assert parsed.hostname == "127.0.0.1"
    assert parsed.path.strip("/") == "smartsell"
    assert source.startswith("DATABASE_URL")


def test_pytest_prefers_test_async_url(monkeypatch):
    _clear_all_db_env(monkeypatch)

    # Enable pytest context
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "testcase")

    # Provide test async URL and a main DATABASE_URL
    monkeypatch.setenv("TEST_ASYNC_DATABASE_URL", "postgresql+asyncpg://postgres:test@127.0.0.1:5432/smartsell_test")
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg2://mainuser:mainpw@127.0.0.1:5432/smartsell")

    url, source, _ = resolve_async_database_url(Settings())
    parsed = urlparse(url)

    assert parsed.scheme.startswith("postgresql+asyncpg")
    assert parsed.path.strip("/") == "smartsell_test"
    assert source.startswith("TEST_ASYNC_DATABASE_URL")
