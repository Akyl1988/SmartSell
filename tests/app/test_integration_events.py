import pytest
import sqlalchemy as sa

from app.models.integration_event import IntegrationEvent
from app.models.marketplace import KaspiStoreToken


class _FakeResponse:
    def __init__(self, status_code: int):
        self.status_code = status_code
        self.content = b""


class _FakeAsyncClient:
    def __init__(self, responses: list[_FakeResponse]):
        self._responses = responses

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, *args, **kwargs):
        return self._responses.pop(0)


@pytest.mark.asyncio
async def test_integration_events_connect_and_selftest(
    async_client, async_db_session, monkeypatch, company_a_admin_headers
):
    payload = {
        "company_name": "Company A",
        "store_name": "store-a",
        "token": "token-a-123456",
        "verify": False,
    }

    async def _upsert_token(session, store_name: str, plaintext_token: str):
        session.add(KaspiStoreToken(store_name=store_name, token_ciphertext=plaintext_token.encode("utf-8")))
        await session.commit()

    async def _get_token(session, store_name: str):
        return "token-a"

    monkeypatch.setattr(KaspiStoreToken, "upsert_token", _upsert_token)
    monkeypatch.setattr(KaspiStoreToken, "get_token", _get_token)

    resp = await async_client.post("/api/v1/kaspi/connect", json=payload, headers=company_a_admin_headers)
    assert resp.status_code == 200, resp.text

    from app.api.v1 import kaspi as kaspi_router

    fake_client = _FakeAsyncClient(
        [
            _FakeResponse(200),
            _FakeResponse(200),
            _FakeResponse(200),
        ]
    )
    monkeypatch.setattr(kaspi_router.httpx, "AsyncClient", lambda *args, **kwargs: fake_client)

    resp = await async_client.get("/api/v1/kaspi/token/selftest", headers=company_a_admin_headers)
    assert resp.status_code == 200, resp.text

    events = (
        (
            await async_db_session.execute(
                sa.select(IntegrationEvent).where(IntegrationEvent.kind.in_(["kaspi_connect", "kaspi_selftest"]))
            )
        )
        .scalars()
        .all()
    )
    assert {e.kind for e in events} >= {"kaspi_connect", "kaspi_selftest"}

    resp = await async_client.get("/api/v1/integrations/events?kind=kaspi&limit=100", headers=company_a_admin_headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert len(data) >= 2
    assert all(str(item["kind"]).startswith("kaspi") for item in data)


@pytest.mark.asyncio
async def test_integration_events_orders_sync_failure(
    async_client,
    async_db_session,
    monkeypatch,
    company_a_admin_headers,
    ensure_company_has_kaspi_store_id,
    kaspi_adapter_health_ok,
):
    from app.api.v1 import kaspi as kaspi_router

    await ensure_company_has_kaspi_store_id()
    _ = kaspi_adapter_health_ok

    async def _sync_orders(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(kaspi_router.KaspiService, "sync_orders", _sync_orders)

    resp = await async_client.post(
        "/api/v1/kaspi/orders/sync?merchantUid=store-a",
        headers=company_a_admin_headers,
    )
    assert resp.status_code == 500, resp.text

    event = (
        (
            await async_db_session.execute(
                sa.select(IntegrationEvent).where(IntegrationEvent.kind == "kaspi_orders_sync")
            )
        )
        .scalars()
        .first()
    )
    assert event is not None
    assert event.status == "failed"
