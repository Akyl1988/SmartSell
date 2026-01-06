import pytest
from sqlalchemy.orm import sessionmaker

import tests.conftest as base_conftest
from app.api.v1 import kaspi as kaspi_module
from app.core.security import create_access_token, get_password_hash
from app.models.user import User

TENANT_ENDPOINTS = [
    ("/api/v1/invoices", "get"),
    ("/api/v1/wallet/accounts", "get"),
    ("/api/v1/payments/", "get"),
    ("/api/v1/subscriptions", "get"),
    ("/api/v1/products", "get"),
    ("/api/v1/analytics/dashboard", "get"),
    ("/api/v1/kaspi/feed", "get"),
]


def _platform_admin_headers_without_company() -> dict[str, str]:
    if base_conftest.sync_engine is None:
        raise RuntimeError("sync_engine is not initialized; ensure test_db fixture runs first")

    SessionLocal = sessionmaker(bind=base_conftest.sync_engine, expire_on_commit=False, autoflush=False)
    with SessionLocal() as s:
        user = s.query(User).filter(User.phone == "+79999990000").first()
        if not user:
            user = User(
                phone="+79999990000",
                company_id=None,
                hashed_password=get_password_hash("Secret123!"),
                role="platform_admin",
                is_active=True,
                is_verified=True,
            )
            s.add(user)
        else:
            user.company_id = None
            user.role = "platform_admin"
            user.is_active = True
            user.is_verified = True
        s.commit()
        s.refresh(user)
        token = create_access_token(subject=user.id, extra={"role": "platform_admin"})
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def platform_admin_no_company_headers(test_db) -> dict[str, str]:
    return _platform_admin_headers_without_company()


async def _call_endpoint(async_client, path: str, method: str, headers: dict[str, str], monkeypatch):
    if path == "/api/v1/kaspi/feed":

        class _FakeKaspiService:
            async def generate_product_feed(self, company_id: int, db):  # noqa: ANN001
                return "<feed/>"

        monkeypatch.setattr(kaspi_module, "KaspiService", _FakeKaspiService)
    requester = getattr(async_client, method)
    return await requester(path, headers=headers)


@pytest.mark.asyncio
@pytest.mark.parametrize("path,method", TENANT_ENDPOINTS)
async def test_platform_admin_without_company_forbidden(
    async_client, platform_admin_no_company_headers, monkeypatch, path, method
):
    resp = await _call_endpoint(async_client, path, method, platform_admin_no_company_headers, monkeypatch)
    assert resp.status_code == 403


@pytest.mark.asyncio
@pytest.mark.parametrize("path,method", TENANT_ENDPOINTS)
async def test_tenant_admin_allowed(async_client, company_a_admin_headers, monkeypatch, path, method):
    resp = await _call_endpoint(async_client, path, method, company_a_admin_headers, monkeypatch)
    assert resp.status_code == 200
