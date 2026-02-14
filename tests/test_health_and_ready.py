import pytest

from app import main as main_mod
from app.core.config import settings


@pytest.mark.asyncio
async def test_health_ok(client):
    r = await client.get("/health")
    assert r.status_code == 200
    j = r.json()
    assert "checks" in j and "version" in j


@pytest.mark.asyncio
async def test_ready_relaxed_200(client, monkeypatch):
    monkeypatch.setenv("READINESS_STRICT", "0")
    r = await client.get("/ready")
    assert r.status_code == 200
    assert "ready" in r.json()


@pytest.mark.asyncio
async def test_ready_strict_db_down_returns_503(client, monkeypatch):
    monkeypatch.setenv("READINESS_STRICT", "1")
    monkeypatch.setattr(settings, "ENVIRONMENT", "production")
    monkeypatch.setattr(settings, "DATABASE_URL", "postgresql://user:pass@localhost:5432/smartsell")

    async def _fail_pg(*_args, **_kwargs):
        return False, "db_down", {}

    monkeypatch.setattr(main_mod, "_pg_probe", _fail_pg)

    r = await client.get("/ready")
    assert r.status_code == 503
    assert r.json().get("postgres", {}).get("ok") is False


@pytest.mark.asyncio
async def test_ready_strict_payments_provider_missing_returns_503(client, monkeypatch):
    monkeypatch.setenv("READINESS_STRICT", "1")
    monkeypatch.setenv("READINESS_REQUIRE_PROVIDERS", "1")
    monkeypatch.setattr(settings, "ENVIRONMENT", "production", raising=False)

    async def _providers():
        return {
            "payments": {"ok": False, "detail": "provider_missing"},
            "otp": {"ok": True, "detail": "ok"},
            "messaging": {"ok": True, "detail": "ok"},
        }

    monkeypatch.setattr(main_mod, "_check_provider_registry", _providers)

    r = await client.get("/ready")
    assert r.status_code == 503
    details = r.json().get("providers", {}).get("details", {})
    assert details.get("payments", {}).get("ok") is False


@pytest.mark.asyncio
async def test_metrics_available(client):
    r = await client.get("/metrics")
    assert r.status_code == 200
