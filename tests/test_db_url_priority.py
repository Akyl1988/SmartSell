import importlib

from app.core import config


def _reset_settings_cache() -> None:
    config.get_settings.cache_clear()  # type: ignore[attr-defined]
    importlib.reload(config)


def test_env_database_url_wins(monkeypatch):
    env_url = "postgresql+asyncpg://postgres:envpass@host1:5432/db_env"

    # Ensure no test overrides
    for key in ("TEST_DATABASE_URL", "DATABASE_TEST_URL"):
        monkeypatch.delenv(key, raising=False)

    monkeypatch.setenv("DATABASE_URL", env_url)

    _reset_settings_cache()
    s = config.get_settings()

    assert s.DATABASE_URL == env_url
    assert config.db_url_fingerprint(s.DATABASE_URL) == config.db_url_fingerprint(env_url)
