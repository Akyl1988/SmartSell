import pytest

from app.models.user import User


def _get_user_by_phone(db_session, phone: str) -> User:
    return db_session.query(User).filter(User.phone == phone).one()


@pytest.mark.asyncio
async def test_invoices_list_same_company_allowed(client, db_session, company_a_admin_headers):
    user_a = _get_user_by_phone(db_session, "+70000010001")
    company_a_id = user_a.company_id

    created = await client.post(
        "/api/v1/invoices",
        json={"amount": "100.00", "currency": "KZT", "status": "draft"},
        headers=company_a_admin_headers,
    )
    assert created.status_code == 201, created.text

    listed = await client.get("/api/v1/invoices", headers=company_a_admin_headers)
    assert listed.status_code == 200, listed.text
    items = listed.json()
    assert any(inv.get("company_id") == company_a_id for inv in items)


@pytest.mark.asyncio
async def test_invoices_list_other_company_forbidden(
    client, db_session, company_a_admin_headers, company_b_admin_headers
):
    user_a = _get_user_by_phone(db_session, "+70000010001")
    company_a_id = user_a.company_id

    created = await client.post(
        "/api/v1/invoices",
        json={"amount": "150.00", "currency": "KZT", "status": "draft"},
        headers=company_a_admin_headers,
    )
    assert created.status_code == 201, created.text

    foreign = await client.get("/api/v1/invoices", headers=company_b_admin_headers)
    assert foreign.status_code == 200, foreign.text
    items = foreign.json()
    assert all(inv.get("company_id") != company_a_id for inv in items)


@pytest.mark.asyncio
async def test_invoices_list_platform_admin_forbidden(client, db_session, company_a_admin_headers, auth_headers):
    user_a = _get_user_by_phone(db_session, "+70000010001")
    company_a_id = user_a.company_id

    created = await client.post(
        "/api/v1/invoices",
        json={"amount": "200.00", "currency": "KZT", "status": "draft"},
        headers=company_a_admin_headers,
    )
    assert created.status_code == 201, created.text

    platform = await client.get("/api/v1/invoices", headers=auth_headers)
    assert platform.status_code == 200, platform.text
    items = platform.json()
    assert all(inv.get("company_id") != company_a_id for inv in items)
