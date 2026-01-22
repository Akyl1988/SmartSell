import httpx
import pytest

from app.models.company import Company
from app.models.kaspi_mc_session import KaspiMcSession
from app.models.kaspi_offer import KaspiOffer
from app.services.kaspi_mc_sync import normalize_mc_offer


class _FakeResponse:
    def __init__(self, status_code: int, json_body: dict | None = None):
        self.status_code = status_code
        self._json_body = json_body or {}
        self.content = b"{}"

    def json(self):
        return self._json_body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                "error",
                request=httpx.Request("GET", "https://mc.shop.kaspi.kz"),
                response=httpx.Response(self.status_code),
            )


class _FakeAsyncClient:
    def __init__(self, responses):
        self._responses = responses

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, *args, **kwargs):
        return self._responses.pop(0) if self._responses else _FakeResponse(200, {"items": []})


async def _ensure_company(async_db_session, company_id: int) -> None:
    company = await async_db_session.get(Company, company_id)
    if not company:
        async_db_session.add(Company(id=company_id, name=f"Company {company_id}"))
        await async_db_session.commit()


def test_normalize_mc_offer_city_prices_and_range():
    item = {
        "sku": "S1",
        "masterSku": "M1",
        "title": "Item",
        "cityPrices": [{"cityId": 750000000, "value": 1200, "oldprice": 1500}],
        "rangePrice": {"MIN": 1000, "MAX": 2000},
        "availabilities": [{"stockCount": 5, "preOrder": True, "stockSpecified": True}],
    }
    normalized = normalize_mc_offer(item)
    assert normalized["price"] == 1200
    assert normalized["old_price"] == 1500
    assert normalized["stock_count"] == 5
    assert normalized["pre_order"] is True
    assert normalized["stock_specified"] is True

    item2 = {
        "sku": "S2",
        "masterSku": "M2",
        "title": "Item2",
        "cityPrices": [{"cityId": 111, "value": 0}],
        "rangePrice": {"MIN": 1000, "MAX": 2000},
        "availabilities": [],
    }
    normalized2 = normalize_mc_offer(item2)
    assert normalized2["price"] == 1000


@pytest.mark.asyncio
async def test_mc_sync_upserts_offers(async_client, async_db_session, company_a_admin_headers, monkeypatch):
    await _ensure_company(async_db_session, 1001)

    async_db_session.add(
        KaspiMcSession(
            company_id=1001,
            merchant_uid="17319385",
            cookies_ciphertext=b"cookie",
            is_active=True,
        )
    )
    await async_db_session.commit()

    responses = [
        _FakeResponse(
            200,
            {
                "items": [
                    {"sku": "S1", "title": "Item 1", "cityPrices": [{"cityId": 750000000, "value": 1000}]},
                    {"sku": "S2", "title": "Item 2", "rangePrice": {"MIN": 2000, "MAX": 2500}},
                    {"sku": "S3", "title": "Item 3", "minPrice": 3000},
                ],
                "total": 5,
            },
        ),
        _FakeResponse(
            200,
            {
                "items": [
                    {"sku": "S4", "title": "Item 4", "maxPrice": 4000},
                    {"sku": "S5", "title": "Item 5", "rangePrice": {"MIN": 5000}},
                ]
            },
        ),
        _FakeResponse(200, {"items": []}),
    ]

    from app.services import kaspi_mc_sync

    monkeypatch.setattr(
        kaspi_mc_sync.httpx,
        "AsyncClient",
        lambda *args, **kwargs: _FakeAsyncClient(responses),
    )

    async def _fake_get_cookies(*args, **kwargs):
        return "a=b; c=d"

    monkeypatch.setattr(KaspiMcSession, "get_cookies", _fake_get_cookies)

    resp = await async_client.post(
        "/api/v1/kaspi/mc/sync",
        headers=company_a_admin_headers,
        params={"merchantUid": "17319385"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "DONE"
    assert data["upserted"] == 5

    # update price for S1
    responses = [_FakeResponse(200, {"items": [{"sku": "S1", "title": "Item 1", "minPrice": 1500}]})]
    monkeypatch.setattr(
        kaspi_mc_sync.httpx,
        "AsyncClient",
        lambda *args, **kwargs: _FakeAsyncClient(responses),
    )

    resp2 = await async_client.post(
        "/api/v1/kaspi/mc/sync",
        headers=company_a_admin_headers,
        params={"merchantUid": "17319385"},
    )
    assert resp2.status_code == 200

    result = await async_db_session.execute(
        KaspiOffer.__table__.select().where(
            KaspiOffer.company_id == 1001,
            KaspiOffer.merchant_uid == "17319385",
            KaspiOffer.sku == "S1",
        )
    )
    row = result.first()
    assert row is not None
    assert float(row.price) == 1500.0
