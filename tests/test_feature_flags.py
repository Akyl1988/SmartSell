import pytest


@pytest.mark.anyio
async def test_feature_flags_crud(client):
    r1 = await client.get("/feature-flags")
    assert r1.status_code == 200

    r2 = await client.put("/feature-flags/demo", json={"enabled": True})
    assert r2.status_code == 200 and r2.json()["enabled"] is True

    r3 = await client.get("/feature-flags/demo")
    assert r3.status_code == 200 and r3.json()["enabled"] is True

    r4 = await client.post("/feature-flags/demo/toggle")
    assert r4.status_code == 200 and isinstance(r4.json()["enabled"], bool)

    r5 = await client.delete("/feature-flags/demo")
    assert r5.status_code == 200
