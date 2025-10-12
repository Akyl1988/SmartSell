import pytest


@pytest.mark.anyio
async def test_security_headers_present(client):
    r = await client.get("/ping")
    h = r.headers
    assert "X-Content-Type-Options" in h
    assert "X-Frame-Options" in h
    assert "Referrer-Policy" in h
    assert "Server" in h
    assert "X-Response-Time-ms" in h
