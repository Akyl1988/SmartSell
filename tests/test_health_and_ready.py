import pytest


@pytest.mark.anyio
async def test_health_ok(client):
    r = await client.get("/health")
    assert r.status_code == 200
    j = r.json()
    assert "checks" in j and "version" in j


@pytest.mark.anyio
async def test_ready_relaxed_200(client, monkeypatch):
    monkeypatch.setenv("READINESS_STRICT", "0")
    r = await client.get("/ready")
    assert r.status_code == 200
    assert "ready" in r.json()


@pytest.mark.anyio
async def test_metrics_available(client):
    r = await client.get("/metrics")
    assert r.status_code == 200
