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
