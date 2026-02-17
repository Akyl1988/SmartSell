from __future__ import annotations

import pytest

from app.models.user import User

pytestmark = pytest.mark.asyncio


async def test_admin_repricing_run_endpoint(async_client, db_session, auth_headers):
    user = db_session.query(User).first()
    assert user is not None
    resp = await async_client.post(
        f"/api/v1/admin/tasks/repricing/run?company_id={user.company_id}",
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert payload.get("run_id")
    assert payload.get("status")
