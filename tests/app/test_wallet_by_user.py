import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User


async def _get_user(async_db_session: AsyncSession, phone: str) -> User:
    res = await async_db_session.execute(select(User).where(User.phone == phone))
    user = res.scalars().first()
    assert user is not None
    return user


@pytest.mark.asyncio
async def test_wallet_by_user_returns_account_for_current_user(
    async_client: AsyncClient,
    async_db_session: AsyncSession,
    company_a_manager_headers,
):
    user = await _get_user(async_db_session, "+70000010002")

    create = await async_client.post(
        "/api/v1/wallet/accounts",
        json={"user_id": user.id, "currency": "KZT"},
        headers=company_a_manager_headers,
    )
    assert create.status_code == 201, create.text

    resp = await async_client.get(
        f"/api/v1/wallet/accounts/by-user?user_id={user.id}&currency=KZT",
        headers=company_a_manager_headers,
    )
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert payload.get("id")


@pytest.mark.asyncio
async def test_wallet_by_user_missing_account_returns_404(
    async_client: AsyncClient,
    async_db_session: AsyncSession,
    company_a_manager_headers,
):
    user = await _get_user(async_db_session, "+70000010002")

    resp = await async_client.get(
        f"/api/v1/wallet/accounts/by-user?user_id={user.id}&currency=USD",
        headers=company_a_manager_headers,
    )
    assert resp.status_code == 404, resp.text
    payload = resp.json()
    assert payload.get("code") == "WALLET_ACCOUNT_NOT_FOUND"


@pytest.mark.asyncio
async def test_wallet_by_user_forbidden_for_non_admin_mismatch(
    async_client: AsyncClient,
    async_db_session: AsyncSession,
    company_a_analyst_headers,
    company_a_admin_headers,
):
    _ = company_a_admin_headers
    admin_user = await _get_user(async_db_session, "+70000010001")

    resp = await async_client.get(
        f"/api/v1/wallet/accounts/by-user?user_id={admin_user.id}&currency=KZT",
        headers=company_a_analyst_headers,
    )
    assert resp.status_code == 403, resp.text
    payload = resp.json()
    assert payload.get("code") == "FORBIDDEN"
