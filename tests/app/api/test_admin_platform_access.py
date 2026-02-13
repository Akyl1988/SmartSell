from __future__ import annotations

import pytest
from sqlalchemy.orm import sessionmaker

import tests.conftest as base_conftest
from app.core.security import create_access_token, get_password_hash
from app.models.user import User

pytestmark = pytest.mark.asyncio


def _superuser_headers_without_company() -> dict[str, str]:
    if base_conftest.sync_engine is None:
        raise RuntimeError("sync_engine is not initialized; ensure test_db fixture runs first")

    SessionLocal = sessionmaker(bind=base_conftest.sync_engine, expire_on_commit=False, autoflush=False)
    with SessionLocal() as s:
        user = s.query(User).filter(User.phone == "+79999990021").first()
        if not user:
            user = User(
                phone="+79999990021",
                company_id=None,
                hashed_password=get_password_hash("Secret123!"),
                role="admin",
                is_superuser=True,
                is_active=True,
                is_verified=True,
            )
            s.add(user)
        else:
            user.company_id = None
            user.role = "admin"
            user.is_superuser = True
            user.is_active = True
            user.is_verified = True
        s.commit()
        s.refresh(user)
        token = create_access_token(subject=user.id)
    return {"Authorization": f"Bearer {token}"}


async def test_admin_tasks_subscriptions_renew_denies_store_admin(async_client, company_a_admin_headers):
    resp = await async_client.post(
        "/api/v1/admin/tasks/subscriptions/renew/run",
        headers=company_a_admin_headers,
    )
    assert resp.status_code == 403, resp.text
    payload = resp.json()
    assert payload.get("code") == "ADMIN_REQUIRED"


async def test_admin_tasks_subscriptions_renew_denies_store_roles(
    async_client,
    company_a_manager_headers,
    company_a_employee_headers,
):
    for headers in (company_a_manager_headers, company_a_employee_headers):
        resp = await async_client.post(
            "/api/v1/admin/tasks/subscriptions/renew/run",
            headers=headers,
        )
        assert resp.status_code == 403, resp.text
        payload = resp.json()
        assert payload.get("code") == "ADMIN_REQUIRED"


async def test_admin_tasks_subscriptions_renew_allows_platform_admin(async_client, auth_headers, test_db):
    _ = test_db
    resp = await async_client.post(
        "/api/v1/admin/tasks/subscriptions/renew/run",
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text


async def test_admin_tasks_subscriptions_renew_allows_superuser(async_client, test_db):
    _ = test_db
    headers = _superuser_headers_without_company()
    resp = await async_client.post(
        "/api/v1/admin/tasks/subscriptions/renew/run",
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
