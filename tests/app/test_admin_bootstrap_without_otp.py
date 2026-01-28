import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.invitation import InvitationToken
from app.models.user import User
from app.services.otp_providers import is_otp_active
from app.utils.tokens import hash_token


def _disable_otp(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OTP_PROVIDER", "noop")
    monkeypatch.setenv("OTP_ENABLED", "1")
    is_otp_active.cache_clear()


@pytest.mark.asyncio
async def test_admin_can_invite_user_without_otp(
    async_client: AsyncClient, async_db_session: AsyncSession, company_a_admin_headers, monkeypatch: pytest.MonkeyPatch
):
    _disable_otp(monkeypatch)

    resp = await async_client.post(
        "/api/v1/auth/invitations",
        headers=company_a_admin_headers,
        json={"email": "otp-off-admin@example.com", "phone": "77001110001", "role": "employee"},
    )
    assert resp.status_code == 200, resp.text


@pytest.mark.asyncio
async def test_self_register_blocked_without_otp(
    async_client: AsyncClient, async_db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
):
    _disable_otp(monkeypatch)

    resp = await async_client.post(
        "/api/v1/auth/register",
        json={"phone": "+77001110002", "password": "Password123!", "company_name": "No OTP Co"},
    )
    assert resp.status_code in {401, 403}


@pytest.mark.asyncio
async def test_connect_store_blocked_without_otp(
    async_client: AsyncClient,
    async_db_session: AsyncSession,
    company_a_manager_headers,
    monkeypatch: pytest.MonkeyPatch,
):
    _disable_otp(monkeypatch)

    resp = await async_client.post(
        "/api/v1/kaspi/connect",
        headers=company_a_manager_headers,
        json={
            "company_name": "Kaspi Co",
            "store_name": "kaspi-store",
            "token": "kaspi-token-1234567890",
            "verify": False,
        },
    )
    assert resp.status_code in {401, 403}


@pytest.mark.asyncio
async def test_invitation_accept_works_without_otp(
    async_client: AsyncClient, async_db_session: AsyncSession, company_a_admin_headers, monkeypatch: pytest.MonkeyPatch
):
    _disable_otp(monkeypatch)

    resp = await async_client.post(
        "/api/v1/auth/invitations",
        headers=company_a_admin_headers,
        json={"email": "otp-off-accept@example.com", "phone": "77001110003", "role": "employee"},
    )
    assert resp.status_code == 200, resp.text

    rows = await async_db_session.execute(select(InvitationToken).order_by(InvitationToken.id.desc()))
    invite = rows.scalars().first()
    assert invite is not None

    token = "invitation-token-otp-off"
    invite.token_hash = hash_token(token)
    await async_db_session.commit()

    accept = await async_client.post(
        "/api/v1/auth/invitations/accept",
        json={"token": token, "password": "Password123!"},
    )
    assert accept.status_code == 200, accept.text

    await async_db_session.rollback()
    res = await async_db_session.execute(select(User).where(User.phone == "77001110003"))
    user = res.scalars().first()
    assert user is not None
