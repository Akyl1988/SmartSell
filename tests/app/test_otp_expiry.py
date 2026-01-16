import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import OtpAttempt
from app.utils.otp import generate_otp_code, hash_otp_code


@pytest.mark.asyncio
async def test_otp_expired_code_rejected(async_client: AsyncClient, async_db_session: AsyncSession):
    phone = "77009998877"
    code = generate_otp_code()
    code_hash = hash_otp_code(code)

    attempt = OtpAttempt.create_new(
        phone=phone,
        code_hash=code_hash,
        purpose="login",
        expires_minutes=1,
        attempts_left=3,
    )
    attempt.expires_at = attempt.expires_at.replace(year=2000)
    async_db_session.add(attempt)
    await async_db_session.commit()

    resp = await async_client.post(
        "/api/v1/auth/verify-otp",
        json={"phone": phone, "purpose": "login", "code": code},
    )
    assert resp.status_code == 422
