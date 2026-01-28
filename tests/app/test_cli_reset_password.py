from __future__ import annotations

import os
import subprocess
import sys
from datetime import timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.security import get_password_hash, verify_password
from app.models import Company, User
from app.models.user import utc_now
from tests.conftest import SYNC_TEST_DATABASE_URL


@pytest.mark.asyncio
async def test_reset_password_cli_updates_hash_and_unlocks(async_db_session: AsyncSession) -> None:
    company = Company(name="CLI Co")
    async_db_session.add(company)
    await async_db_session.flush()

    user = User(
        company_id=company.id,
        phone="77079990011",
        email="cli.reset.77079990011@example.com",
        hashed_password=get_password_hash("OldPass123!"),
        role="admin",
        is_active=True,
        is_verified=True,
        failed_login_attempts=3,
        locked_until=utc_now() + timedelta(minutes=10),
        locked_at=utc_now(),
    )
    async_db_session.add(user)
    await async_db_session.commit()

    old_hash = user.hashed_password

    env = os.environ.copy()
    env["DATABASE_URL"] = SYNC_TEST_DATABASE_URL
    env["DB_URL"] = SYNC_TEST_DATABASE_URL
    env.setdefault("TESTING", "1")
    cmd = [
        sys.executable,
        "-m",
        "app.cli.reset_password",
        "--identifier",
        "cli.reset.77079990011@example.com",
        "--password",
        "NewPass123!",
        "--unlock",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, env=env, check=False)

    assert result.returncode == 0
    assert "user_id=" in result.stdout
    assert "identifier=cli.reset.77079990011@example.com" in result.stdout
    assert "updated" in result.stdout
    assert "unlock=True" in result.stdout
    assert result.stderr == ""

    sessionmaker = async_sessionmaker(async_db_session.bind, expire_on_commit=False)
    async with sessionmaker() as session:
        refreshed = (await session.execute(select(User).where(User.id == user.id))).scalars().first()
    assert refreshed is not None
    assert refreshed.hashed_password != old_hash
    assert verify_password("NewPass123!", refreshed.hashed_password)
    assert refreshed.failed_login_attempts == 0
    assert refreshed.locked_until is None
    assert refreshed.locked_at is None
