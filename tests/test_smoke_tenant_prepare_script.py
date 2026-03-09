from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_smoke_tenant_prepare_uses_existing_subscription_paths() -> None:
    content = (ROOT / "scripts/smoke-tenant-prepare.ps1").read_text(encoding="utf-8")
    assert "/api/v1/subscriptions/current" in content
    assert "/api/v1/subscriptions/$($previousState.id)/renew" in content
    assert "/api/v1/subscriptions/$($resultingState.id)" in content
    assert "plan = \"Start\"" in content
    assert "plan = \"pro\"" in content
    assert "Test-SmokeTenantProductCreatePreflight" in content


def test_smoke_tenant_prepare_prints_required_outputs() -> None:
    content = (ROOT / "scripts/smoke-tenant-prepare.ps1").read_text(encoding="utf-8")
    assert "TENANT_COMPANY_ID=" in content
    assert "PREVIOUS_SUBSCRIPTION_STATE=" in content
    assert "ACTION_TAKEN=" in content
    assert "RESULTING_SUBSCRIPTION_STATE=" in content
    assert "SMOKE_ALLOWED=" in content
