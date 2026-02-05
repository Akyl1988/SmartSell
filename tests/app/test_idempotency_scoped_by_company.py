from __future__ import annotations

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_idempotency_scoped_by_company(
    async_client: AsyncClient,
    company_a_admin_headers: dict[str, str],
    company_b_admin_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
):
    async def _fake_export(*args, **kwargs):
        return "/tmp/fake.xlsx"

    monkeypatch.setattr("app.api.v1.analytics.export_analytics_to_excel", _fake_export)

    payload = {"export_type": "sales", "format": "xlsx"}
    key = "idem-same-key"

    resp_a = await async_client.post(
        "/api/v1/analytics/export",
        json=payload,
        headers={**company_a_admin_headers, "Idempotency-Key": key},
    )
    assert resp_a.status_code == 200, resp_a.text

    resp_b = await async_client.post(
        "/api/v1/analytics/export",
        json=payload,
        headers={**company_b_admin_headers, "Idempotency-Key": key},
    )
    assert resp_b.status_code == 200, resp_b.text
