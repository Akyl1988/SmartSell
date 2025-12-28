from fastapi.testclient import TestClient
from sqlalchemy.engine.url import make_url

from app.api.v1 import debug_db as dbg
from app.core import config


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

    import importlib

    import app.api.routes as routes  # type: ignore
    import app.main as main  # type: ignore

    importlib.reload(routes)
    importlib.reload(main)
    app = main.app

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
