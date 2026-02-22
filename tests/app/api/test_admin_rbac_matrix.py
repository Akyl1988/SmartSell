from __future__ import annotations

import pytest

from app.api.v1 import kaspi as kaspi_module

pytestmark = pytest.mark.asyncio


def _allow_feature(_feature: str):
    async def _dep(*, request, current_user, db):  # noqa: ANN001
        return current_user

    return _dep


async def test_admin_tasks_platform_only(
    async_client,
    auth_headers,
    company_a_admin_headers,
    company_a_manager_headers,
    company_a_employee_headers,
):
    resp = await async_client.post(
        "/api/v1/admin/tasks/subscriptions/renew/run",
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text

    resp_store_admin = await async_client.post(
        "/api/v1/admin/tasks/subscriptions/renew/run",
        headers=company_a_admin_headers,
    )
    assert resp_store_admin.status_code == 403, resp_store_admin.text
    payload = resp_store_admin.json()
    assert payload.get("code") == "ADMIN_REQUIRED"

    resp_regular = await async_client.post(
        "/api/v1/admin/tasks/subscriptions/renew/run",
        headers=company_a_manager_headers,
    )
    assert resp_regular.status_code == 403, resp_regular.text
    payload_regular = resp_regular.json()
    assert payload_regular.get("code") == "ADMIN_REQUIRED"

    resp_employee = await async_client.post(
        "/api/v1/admin/tasks/subscriptions/renew/run",
        headers=company_a_employee_headers,
    )
    assert resp_employee.status_code == 403, resp_employee.text
    payload_employee = resp_employee.json()
    assert payload_employee.get("code") == "ADMIN_REQUIRED"


async def test_kaspi_orders_store_admin_only(
    async_client,
    async_db_session,
    company_a_admin_headers,
    company_a_manager_headers,
    company_a_employee_headers,
    auth_headers,
    monkeypatch,
):
    from app.models.company import Company

    company = await async_db_session.get(Company, 1001)
    if company is None:
        company = Company(id=1001, name="Company 1001", kaspi_store_id="123")
        async_db_session.add(company)
    else:
        company.kaspi_store_id = "123"
    await async_db_session.commit()

    monkeypatch.setattr(kaspi_module, "require_feature", _allow_feature)

    resp_store_admin = await async_client.get(
        "/api/v1/kaspi/orders",
        headers=company_a_admin_headers,
    )
    assert resp_store_admin.status_code == 200, resp_store_admin.text

    resp_platform_admin = await async_client.get(
        "/api/v1/kaspi/orders",
        headers=auth_headers,
    )
    assert resp_platform_admin.status_code == 403, resp_platform_admin.text

    resp_manager = await async_client.get(
        "/api/v1/kaspi/orders",
        headers=company_a_manager_headers,
    )
    assert resp_manager.status_code == 403, resp_manager.text

    resp_employee = await async_client.get(
        "/api/v1/kaspi/orders",
        headers=company_a_employee_headers,
    )
    assert resp_employee.status_code == 403, resp_employee.text
