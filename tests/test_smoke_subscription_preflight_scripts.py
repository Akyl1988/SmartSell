from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(rel_path: str) -> str:
    return (ROOT / rel_path).read_text(encoding="utf-8")


def test_smoke_lib_defines_subscription_preflight() -> None:
    content = _read("scripts/_smoke-lib.ps1")
    assert "function Test-SmokeTenantProductCreatePreflight" in content
    assert "SMOKE_PRECHECK_SUBSCRIPTION_BLOCK" in content
    assert "/api/v1/products?page=1&per_page=1" in content


def test_preorders_smoke_calls_preflight_before_product_create() -> None:
    content = _read("scripts/smoke-preorders-e2e.ps1")
    preflight_call = "Test-SmokeTenantProductCreatePreflight"
    create_product_call = 'Invoke-Api -Method "POST" -Url "$BaseUrl/api/v1/products"'
    assert preflight_call in content
    assert create_product_call in content
    assert content.index(preflight_call) < content.index(create_product_call)


def test_orders_smoke_calls_preflight_before_product_create() -> None:
    content = _read("scripts/smoke-orders-lifecycle.ps1")
    preflight_call = "Test-SmokeTenantProductCreatePreflight"
    create_product_call = 'Invoke-Api -Method "POST" -Url "$BaseUrl/api/v1/products"'
    assert preflight_call in content
    assert create_product_call in content
    assert content.index(preflight_call) < content.index(create_product_call)
