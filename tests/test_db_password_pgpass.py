from urllib.parse import urlparse

from app.core.config import Settings, resolve_database_url


def _clear_db_env(monkeypatch):
    for key in (
        "TEST_DATABASE_URL",
        "TEST_ASYNC_DATABASE_URL",
        "DATABASE_TEST_URL",
        "DATABASE_URL",
        "DB_URL",
        "DB_PASSWORD",
        "PGPASSWORD",
    ):
        monkeypatch.delenv(key, raising=False)


def test_pgpassword_injected_when_url_missing_password(monkeypatch):
    # Simulate local/pytest context with PGPASSWORD provided and URL lacking password
    monkeypatch.setenv("ENVIRONMENT", "local")
    _clear_db_env(monkeypatch)

    url_without_password = "postgresql+asyncpg://postgres@localhost:5432/smartsell_test"
    # testing flag keeps resolution predictable; provide test URL without password
    monkeypatch.setenv("TEST_DATABASE_URL", url_without_password)
    monkeypatch.setenv("PGPASSWORD", "secret_pw")
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "config::pgpass")

    url, source, _ = resolve_database_url(Settings())
    parsed = urlparse(url)

    assert parsed.scheme.startswith("postgres")
    assert parsed.password == "secret_pw"
    assert source == "TEST_DATABASE_URL"


def test_password_borrowed_from_database_url(monkeypatch):
    # If test URL lacks password and DATABASE_URL has one, borrow it
    monkeypatch.setenv("ENVIRONMENT", "local")
    _clear_db_env(monkeypatch)

    test_url = "postgresql+asyncpg://postgres@localhost:5432/smartsell_test"
    base_url = "postgresql+asyncpg://postgres:from_base@localhost:5432/smartsell_test"

    monkeypatch.setenv("TEST_DATABASE_URL", test_url)
    monkeypatch.setenv("DATABASE_URL", base_url)
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "config::pgpass2")

    url, source, _ = resolve_database_url(Settings())
    parsed = urlparse(url)

    assert parsed.password == "from_base"
    assert source == "TEST_DATABASE_URL"


def test_masked_password_is_replaced_by_db_password(monkeypatch):
    _clear_db_env(monkeypatch)
    monkeypatch.setenv("ENVIRONMENT", "testing")
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://user:***@localhost:5432/dbname")
    monkeypatch.setenv("TEST_DATABASE_URL", "postgresql+asyncpg://user:***@localhost:5432/dbname")
    monkeypatch.setenv("DB_PASSWORD", "secret123")

    from app.core.config import Settings, resolve_database_url

    url, source, _ = resolve_database_url(Settings())

    assert "secret123" in url
    assert ":***@" not in url
    assert source


def test_sync_url_injects_password_from_db_password(monkeypatch):
    import os

    from app.core.config import _inject_password_if_missing
    from app.core.db import _normalize_pg_to_psycopg2

    _clear_db_env(monkeypatch)
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.delenv("TESTING", raising=False)

    monkeypatch.setenv("DATABASE_URL", "postgresql://postgres@localhost:5432/smartsell")
    monkeypatch.setenv("DB_PASSWORD", "sync_pw")
    monkeypatch.setenv("PGPASSWORD", "sync_pw")
    os.environ["DB_PASSWORD"] = "sync_pw"
    os.environ["PGPASSWORD"] = "sync_pw"

    injected = _inject_password_if_missing("postgresql://postgres@localhost:5432/smartsell")
    url = _normalize_pg_to_psycopg2(injected)
    parsed = urlparse(url)

    assert parsed.password
