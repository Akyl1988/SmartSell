from urllib.parse import urlparse

from app.core.config import Settings, resolve_async_database_url


def _clear_db_env(monkeypatch):
    for key in (
        "TEST_ASYNC_DATABASE_URL",
        "TEST_DATABASE_URL",
        "TEST_ASYNC_DATABASE_URL",
        "DATABASE_URL",
        "DB_URL",
        "DB_PASSWORD",
        "PGPASSWORD",
    ):
        monkeypatch.delenv(key, raising=False)


def test_async_url_prefers_test_async(monkeypatch):
    _clear_db_env(monkeypatch)
    monkeypatch.setenv(
        "TEST_ASYNC_DATABASE_URL",
        "postgresql+asyncpg://postgres:admin123@127.0.0.1:5432/smartsell_test",
    )

    url, source, _ = resolve_async_database_url(Settings())
    parsed = urlparse(url)

    assert source.startswith("TEST_ASYNC_DATABASE_URL")
    assert parsed.scheme.startswith("postgresql+asyncpg")
    assert parsed.password == "admin123"


def test_async_from_test_database_url_psycopg(monkeypatch):
    _clear_db_env(monkeypatch)
    monkeypatch.setenv(
        "TEST_DATABASE_URL",
        "postgresql+psycopg2://postgres:admin123@127.0.0.1:5432/smartsell_test",
    )

    url, source, _ = resolve_async_database_url(Settings())
    parsed = urlparse(url)

    assert source.startswith("TEST_DATABASE_URL")
    assert parsed.scheme.startswith("postgresql+asyncpg")
    assert parsed.password == "admin123"


def test_async_password_injected_from_pgpassword(monkeypatch):
    _clear_db_env(monkeypatch)
    monkeypatch.setenv("TEST_DATABASE_URL", "postgresql+asyncpg://postgres@127.0.0.1:5432/smartsell_test")
    monkeypatch.setenv("PGPASSWORD", "admin123")

    url, source, _ = resolve_async_database_url(Settings())
    parsed = urlparse(url)

    assert source.startswith("TEST_DATABASE_URL")
    assert parsed.scheme.startswith("postgresql+asyncpg")
    assert parsed.password == "admin123"
