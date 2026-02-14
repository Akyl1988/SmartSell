import pytest


@pytest.mark.asyncio
async def test_openapi_hides_legacy_api_and_exposes_v1(async_client):
    response = await async_client.get("/openapi.json")
    assert response.status_code == 200
    payload = response.json()
    paths = payload.get("paths", {})

    assert "/api/auth/login" not in paths
    assert "/api/v1/auth/login" in paths

    assert "/api/health" not in paths
    assert "/api/v1/health" in paths
    assert "/health" in paths

    assert "/api/wallet/health" not in paths
    assert "/api/v1/wallet/health" in paths

    admin_topup = paths.get("/api/v1/admin/wallet/topup", {})
    assert "post" in admin_topup

    renew_run = paths.get("/api/v1/admin/tasks/subscriptions/renew/run", {})
    assert "post" in renew_run

    campaigns_run = paths.get("/api/v1/admin/tasks/campaigns/run", {})
    assert "post" in campaigns_run

    campaigns_process = paths.get("/api/v1/admin/tasks/campaigns/process/run", {})
    assert "post" in campaigns_process

    campaigns_cleanup = paths.get("/api/v1/admin/tasks/campaigns/cleanup/run", {})
    assert "post" in campaigns_cleanup

    wallet_report = paths.get("/api/v1/reports/wallet/transactions.csv", {})
    assert "get" in wallet_report
    report_content = wallet_report.get("get", {}).get("responses", {}).get("200", {}).get("content", {})
    assert "text/csv" in report_content


@pytest.mark.asyncio
async def test_openapi_kaspi_catalog_template_has_binary_types(async_client):
    response = await async_client.get("/openapi.json")
    assert response.status_code == 200
    payload = response.json()
    responses = (
        payload.get("paths", {})
        .get("/api/v1/kaspi/catalog/template", {})
        .get("get", {})
        .get("responses", {})
        .get("200", {})
    )
    content = responses.get("content", {})

    assert "text/csv" in content
    assert "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" in content
