from __future__ import annotations

from pathlib import Path

from scripts import openapi_report


def test_openapi_report_sections_and_entries() -> None:
    sample_path = Path(__file__).resolve().parents[1] / "fixtures" / "openapi_sample.json"
    spec = openapi_report._load_openapi(sample_path)
    report = openapi_report.build_report(spec)
    normalized = report.replace("`", "")

    assert "## Admin endpoints" in normalized
    assert "GET /api/v1/admin/campaigns/{id}" in normalized
    assert "operationId: adminGetCampaign" in normalized

    assert "## Kaspi endpoints" in normalized
    assert "POST /api/v1/kaspi/ping" in normalized
    assert "operationId: kaspiPing" in normalized

    assert "## Auth" in normalized
    assert "HTTPBearer" in normalized
