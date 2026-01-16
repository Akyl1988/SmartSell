from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.engine.url import make_url

from app.api.v1 import debug_db as dbg
from app.core import config


class _DummyConn:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def execute(self, *args, **kwargs):
        return None


class _DummyEngine:
    def __init__(self):
        self.sync_engine = None

    def connect(self):
        return _DummyConn()


def _prep_env(monkeypatch, env: dict[str, str]) -> None:
    for key in (
        "TESTING",
        "TEST_DATABASE_URL",
        "TEST_ASYNC_DATABASE_URL",
        "DATABASE_TEST_URL",
        "DATABASE_URL",
        "DB_URL",
        "ENVIRONMENT",
        "DEBUG",
    ):
        monkeypatch.delenv(key, raising=False)

    for k, v in env.items():
        monkeypatch.setenv(k, v)

    config.get_settings.cache_clear()  # type: ignore[attr-defined]


def _mount_client(monkeypatch, env: dict[str, str]) -> TestClient:
    _prep_env(monkeypatch, env)
    cfg = config.get_settings()
    monkeypatch.setattr(config, "settings", cfg, raising=False)

    from app.api import routes

    app = FastAPI()
    routes.mount_v1(app, base_prefix=getattr(cfg, "API_V1_STR", "/api/v1") or "/api/v1")
    return TestClient(app)


def test_debug_db_endpoint_exposes_fingerprints(monkeypatch, test_db):
    import tests.conftest as cft

    assert cft.test_engine is not None  # created by test_db fixture

    # align settings with test DSNs and reuse the already-initialized test engine
    monkeypatch.setenv("TEST_ASYNC_DATABASE_URL", cft.TEST_DATABASE_URL)
    monkeypatch.setenv("TEST_DATABASE_URL", cft.SYNC_TEST_DATABASE_URL)
    monkeypatch.setenv("DATABASE_URL", cft.SYNC_TEST_DATABASE_URL)
    monkeypatch.setenv("DB_URL", cft.SYNC_TEST_DATABASE_URL)
    monkeypatch.setenv("ENVIRONMENT", "local")
    config.get_settings.cache_clear()  # type: ignore[attr-defined]
    refreshed = config.get_settings()
    monkeypatch.setattr(config, "settings", refreshed, raising=False)

    monkeypatch.setattr(dbg, "_get_async_engine", lambda: cft.test_engine)

    from app.api import routes
    from app.main import app

    # ensure debug routes are mounted with current settings
    routes.mount_v1(app, base_prefix=getattr(config.settings, "API_V1_STR", "/api/v1") or "/api/v1")

    client = TestClient(app)

    resp = client.get("/api/v1/_debug/db")
    assert resp.status_code == 200
    payload = resp.json()

    assert payload["driver"].startswith("postgresql+")
    assert payload["db"] == make_url(cft.TEST_DATABASE_URL).database
    assert payload["url_fp"]
    assert payload["url_no_pw_fp"]
    assert payload["source"]
    assert "connectivity" in payload

    config.get_settings.cache_clear()  # type: ignore[attr-defined]


def test_debug_routes_enabled_with_debug_flag(monkeypatch):
    dummy_engine = _DummyEngine()

    env = {
        "TESTING": "1",
        "TEST_DATABASE_URL": "postgresql://user:pass@host:5432/db",
        "ENVIRONMENT": "development",
        "DEBUG": "1",
    }

    client = _mount_client(monkeypatch, env)
    monkeypatch.setattr(dbg, "_get_async_engine", lambda: dummy_engine)

    resp = client.get("/api/v1/_debug/db")
    assert resp.status_code == 200


def test_debug_routes_enabled_for_local_env(monkeypatch):
    dummy_engine = _DummyEngine()

    env = {
        "TESTING": "1",
        "TEST_DATABASE_URL": "postgresql://user:pass@host:5432/db",
        "ENVIRONMENT": "local",
        "DEBUG": "0",
    }

    client = _mount_client(monkeypatch, env)
    monkeypatch.setattr(dbg, "_get_async_engine", lambda: dummy_engine)

    resp = client.get("/api/v1/_debug/db")
    assert resp.status_code == 200


def test_debug_routes_disabled_in_non_local_without_debug(monkeypatch):
    env = {
        "TESTING": "1",
        "TEST_DATABASE_URL": "postgresql://user:pass@host:5432/db",
        "ENVIRONMENT": "production",
        "DEBUG": "0",
    }

    client = _mount_client(monkeypatch, env)

    resp = client.get("/api/v1/_debug/db")
    assert resp.status_code == 404
