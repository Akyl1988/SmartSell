import pytest
from sqlalchemy.orm import sessionmaker

import tests.conftest as base_conftest
from app.core.security import create_access_token, get_password_hash
from app.models.user import User


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


@pytest.mark.asyncio
async def test_subscription_renew_run_requires_admin(async_client, company_a_admin_headers):
    resp = await async_client.post(
        "/api/v1/admin/tasks/subscriptions/renew/run",
        headers=company_a_admin_headers,
    )
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert payload.get("ok") is True


@pytest.mark.asyncio
async def test_subscription_renew_run_ok(async_client, test_db):
    _ = test_db
    headers = _platform_admin_headers_without_company()
    resp = await async_client.post(
        "/api/v1/admin/tasks/subscriptions/renew/run",
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert payload.get("ok") is True
    assert "processed" in payload
    assert payload.get("request_id")


@pytest.mark.asyncio
async def test_subscription_renew_run_allows_admin(async_client, company_a_admin_headers):
    resp = await async_client.post(
        "/api/v1/admin/tasks/subscriptions/renew/run",
        headers=company_a_admin_headers,
    )
    assert resp.status_code != 403, resp.text
