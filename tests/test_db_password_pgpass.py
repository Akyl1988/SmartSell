from urllib.parse import urlparse

from app.core.config import Settings, resolve_async_database_url, resolve_database_url


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


def test_masked_password_is_stripped_without_env_password(monkeypatch):
    _clear_db_env(monkeypatch)
    monkeypatch.setenv("ENVIRONMENT", "testing")
    monkeypatch.setenv("TEST_DATABASE_URL", "postgresql+asyncpg://user:***@localhost:5432/dbname")
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "config::masked-no-env")

    url, source, _ = resolve_database_url(Settings())
    parsed = urlparse(url)

    assert ":***@" not in url
    assert parsed.password in {None, ""}
    assert source


def test_async_resolution_strips_masked_password(monkeypatch):
    _clear_db_env(monkeypatch)
    monkeypatch.setenv("ENVIRONMENT", "testing")
    monkeypatch.setenv("TEST_ASYNC_DATABASE_URL", "postgresql+asyncpg://user:***@localhost:5432/dbname")
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "config::async-masked")

    url, _, _ = resolve_async_database_url(Settings())

    assert ":***@" not in url


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


def test_resolved_url_and_sqlalchemy_urls_unmasked(monkeypatch):
    from app.core.config import get_settings

    _clear_db_env(monkeypatch)
    monkeypatch.setenv("ENVIRONMENT", "testing")
    monkeypatch.setenv("TEST_DATABASE_URL", "postgresql://user:realpass@localhost:5432/dbname")
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "config::unmasked")

    get_settings.cache_clear()
    url, _, _ = resolve_database_url(Settings())
    parsed = urlparse(url)

    assert parsed.password
    assert parsed.password != "***"

    settings = get_settings()
    assert settings.sqlalchemy_urls["sync"]
    assert ":***@" not in settings.sqlalchemy_urls["sync"]
    get_settings.cache_clear()


def test_resolve_sync_pg_url_skips_masked_candidate(monkeypatch):
    import app.core.db as db
    from app.core.config import Settings as SettingsCls

    _clear_db_env(monkeypatch)
    monkeypatch.setenv("ENVIRONMENT", "testing")
    monkeypatch.setenv("TEST_ASYNC_DATABASE_URL", "postgresql+asyncpg://user:***@localhost:5432/dbname")
    monkeypatch.setenv("TEST_DATABASE_URL", "postgresql+asyncpg://user:***@localhost:5432/dbname")
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "db::resolve")

    new_settings = SettingsCls()
    monkeypatch.setattr(db, "settings", new_settings, raising=False)

    resolved = db._resolve_sync_pg_url()

    assert ":***@" not in resolved


def test_sync_engine_falls_back_from_masked_url(monkeypatch):
    from sqlalchemy import create_engine as sa_create_engine

    import app.core.db as db

    db._SYNC_ENGINE = None
    db._SYNC_SESSION_MAKER = None
    db._SYNC_REPLICA_ENGINE = None

    calls = {"count": 0, "url": None}
    masked = "postgresql+psycopg2://user:***@localhost:5432/dbname"
    unmasked = "postgresql+psycopg2://user:realpass@localhost:5432/dbname"

    def fake_resolve_sync_pg_url():
        calls["count"] += 1
        return masked if calls["count"] == 1 else unmasked

    def fake_create_engine(url, **kwargs):
        calls["url"] = url
        return sa_create_engine("sqlite:///:memory:")

    monkeypatch.setattr(db, "_resolve_sync_pg_url", fake_resolve_sync_pg_url)
    monkeypatch.setattr(db, "create_engine", fake_create_engine)

    engine = db._get_sync_engine()

    assert calls["url"] == unmasked
    assert calls["count"] >= 2
    assert engine is not None


def test_sqlalchemy_sync_url_preserves_explicit_sslmode_disable_in_prod(monkeypatch):
    from app.core.config import Settings as SettingsCls

    _clear_db_env(monkeypatch)
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://postgres:admin123@127.0.0.1:5432/smartsell_main?sslmode=disable")
    monkeypatch.delenv("POSTGRES_SSLMODE", raising=False)

    s = SettingsCls()
    async_url = s.sqlalchemy_async_url
    sync_url = s.sqlalchemy_sync_url

    assert async_url is not None
    assert "sslmode=disable" in async_url
    assert "sslmode=require" not in async_url
    assert sync_url is not None
    assert "sslmode=disable" in sync_url
    assert "sslmode=require" not in sync_url


def test_sqlalchemy_sync_url_uses_postgres_sslmode_override_when_set(monkeypatch):
    from app.core.config import Settings as SettingsCls

    _clear_db_env(monkeypatch)
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://postgres:admin123@127.0.0.1:5432/smartsell_main?sslmode=disable")
    monkeypatch.setenv("POSTGRES_SSLMODE", "require")

    s = SettingsCls()
    async_url = s.sqlalchemy_async_url
    sync_url = s.sqlalchemy_sync_url

    assert async_url is not None
    assert "sslmode=require" in async_url
    assert sync_url is not None
    assert "sslmode=require" in sync_url


def test_sqlalchemy_urls_do_not_force_sslmode_require_without_override(monkeypatch):
    from app.core.config import Settings as SettingsCls

    _clear_db_env(monkeypatch)
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://postgres:admin123@127.0.0.1:5432/smartsell_main")
    monkeypatch.delenv("POSTGRES_SSLMODE", raising=False)

    s = SettingsCls()
    async_url = s.sqlalchemy_async_url
    sync_url = s.sqlalchemy_sync_url

    assert async_url is not None
    assert "sslmode=require" not in async_url
    assert sync_url is not None
    assert "sslmode=require" not in sync_url
