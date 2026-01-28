import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User
from app.storage.wallet_sql import wallet_ledger


async def _get_user(async_db_session: AsyncSession, phone: str) -> User:
    res = await async_db_session.execute(select(User).where(User.phone == phone))
    user = res.scalars().first()
    assert user is not None
    return user


async def _ledger_count_by_request_id(async_db_session: AsyncSession, request_id: str) -> int:
    await async_db_session.rollback()
    stmt = select(func.count()).select_from(wallet_ledger).where(wallet_ledger.c.client_request_id == request_id)
    return int((await async_db_session.execute(stmt)).scalar_one())


async def _get_balance(async_client: AsyncClient, account_id: int, headers: dict[str, str]) -> str:
    resp = await async_client.get(f"/api/v1/wallet/accounts/{account_id}/balance", headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()["balance"]


@pytest.mark.asyncio
async def test_wallet_idempotency(async_client: AsyncClient, async_db_session: AsyncSession, company_a_admin_headers):
    user_a = await _get_user(async_db_session, "+70000010001")

    acc_a_resp = await async_client.post(
        "/api/v1/wallet/accounts",
        json={"user_id": user_a.id, "currency": "KZT"},
        headers=company_a_admin_headers,
    )
    assert acc_a_resp.status_code == 201, acc_a_resp.text
    account_a_id = acc_a_resp.json()["id"]

    acc_b_resp = await async_client.post(
        "/api/v1/wallet/accounts",
        json={"user_id": user_a.id, "currency": "USD"},
        headers=company_a_admin_headers,
    )
    assert acc_b_resp.status_code == 201, acc_b_resp.text
    account_b_id = acc_b_resp.json()["id"]

    dep_id = str(uuid.uuid4())
    ledger_before = await _ledger_count_by_request_id(async_db_session, dep_id)
    balance_before = await _get_balance(async_client, account_a_id, company_a_admin_headers)
    first = await async_client.post(
        f"/api/v1/wallet/accounts/{account_a_id}/deposit",
        json={"amount": "100.00", "reference": "seed"},
        headers={**company_a_admin_headers, "X-Request-Id": dep_id},
    )
    assert first.status_code == 200, first.text
    ledger_after_first = await _ledger_count_by_request_id(async_db_session, dep_id)
    assert ledger_after_first == ledger_before + 1
    balance_after_first = await _get_balance(async_client, account_a_id, company_a_admin_headers)
    assert balance_after_first != balance_before

    second = await async_client.post(
        f"/api/v1/wallet/accounts/{account_a_id}/deposit",
        json={"amount": "100.00", "reference": "seed"},
        headers={**company_a_admin_headers, "X-Request-Id": dep_id},
    )
    assert second.status_code == 200, second.text
    ledger_after_second = await _ledger_count_by_request_id(async_db_session, dep_id)
    assert ledger_after_second == ledger_after_first
    balance_after_second = await _get_balance(async_client, account_a_id, company_a_admin_headers)
    assert balance_after_second == balance_after_first

    wd_id = str(uuid.uuid4())
    ledger_before = await _ledger_count_by_request_id(async_db_session, wd_id)
    balance_before = await _get_balance(async_client, account_a_id, company_a_admin_headers)
    first = await async_client.post(
        f"/api/v1/wallet/accounts/{account_a_id}/withdraw",
        json={"amount": "30.00", "reference": "wd"},
        headers={**company_a_admin_headers, "X-Request-Id": wd_id},
    )
    assert first.status_code == 200, first.text
    ledger_after_first = await _ledger_count_by_request_id(async_db_session, wd_id)
    assert ledger_after_first == ledger_before + 1
    balance_after_first = await _get_balance(async_client, account_a_id, company_a_admin_headers)
    assert balance_after_first != balance_before

    second = await async_client.post(
        f"/api/v1/wallet/accounts/{account_a_id}/withdraw",
        json={"amount": "30.00", "reference": "wd"},
        headers={**company_a_admin_headers, "X-Request-Id": wd_id},
    )
    assert second.status_code == 200, second.text
    ledger_after_second = await _ledger_count_by_request_id(async_db_session, wd_id)
    assert ledger_after_second == ledger_after_first
    balance_after_second = await _get_balance(async_client, account_a_id, company_a_admin_headers)
    assert balance_after_second == balance_after_first

    tx_id = str(uuid.uuid4())
    ledger_before = await _ledger_count_by_request_id(async_db_session, tx_id)
    first = await async_client.post(
        "/api/v1/wallet/transfer",
        json={
            "source_account_id": account_a_id,
            "destination_account_id": account_b_id,
            "amount": "20.00",
            "reference": "tx",
        },
        headers={**company_a_admin_headers, "X-Request-Id": tx_id},
    )
    assert first.status_code in {200, 409}, first.text
    ledger_after_first = await _ledger_count_by_request_id(async_db_session, tx_id)
    src_after_first = await _get_balance(async_client, account_a_id, company_a_admin_headers)
    dst_after_first = await _get_balance(async_client, account_b_id, company_a_admin_headers)

    second = await async_client.post(
        "/api/v1/wallet/transfer",
        json={
            "source_account_id": account_a_id,
            "destination_account_id": account_b_id,
            "amount": "20.00",
            "reference": "tx",
        },
        headers={**company_a_admin_headers, "X-Request-Id": tx_id},
    )
    assert second.status_code in {200, 409}, second.text
    ledger_after_second = await _ledger_count_by_request_id(async_db_session, tx_id)
    assert ledger_after_second == ledger_after_first
    assert await _get_balance(async_client, account_a_id, company_a_admin_headers) == src_after_first
    assert await _get_balance(async_client, account_b_id, company_a_admin_headers) == dst_after_first

