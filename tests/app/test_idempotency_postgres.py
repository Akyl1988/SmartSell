from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.idempotency import IdempotencyEnforcer


@pytest.mark.asyncio
async def test_idempotency_reserve_and_replay(async_db_session: AsyncSession):
    enforcer = IdempotencyEnforcer(default_ttl=60)

    allowed, status = await enforcer.reserve(
        async_db_session, company_id=1, key="abc", ttl_seconds=60
    )
    assert allowed is True
    assert status is None

    allowed, status = await enforcer.reserve(
        async_db_session, company_id=1, key="abc", ttl_seconds=60
    )
    assert allowed is False
    assert status is None

    await enforcer.set_result(async_db_session, company_id=1, key="abc", status_code=200, ttl_seconds=60)

    allowed, status = await enforcer.reserve(
        async_db_session, company_id=1, key="abc", ttl_seconds=60
    )
    assert allowed is False
    assert status == 200


@pytest.mark.asyncio
async def test_idempotency_tenant_scoped(async_db_session: AsyncSession):
    enforcer = IdempotencyEnforcer(default_ttl=60)

    allowed, _ = await enforcer.reserve(
        async_db_session, company_id=1, key="shared", ttl_seconds=60
    )
    assert allowed is True

    allowed, status = await enforcer.reserve(
        async_db_session, company_id=2, key="shared", ttl_seconds=60
    )
    assert allowed is True
    assert status is None
