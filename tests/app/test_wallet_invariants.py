from __future__ import annotations

import uuid
from decimal import ROUND_HALF_UP, Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User
from app.storage.wallet_sql import wallet_ledger

_DECIMAL_PLACES = Decimal("0.000001")


def _q(value: Decimal | str | int | float) -> Decimal:
    return Decimal(str(value)).quantize(_DECIMAL_PLACES, rounding=ROUND_HALF_UP)


async def _get_user(async_db_session: AsyncSession, phone: str) -> User:
    res = await async_db_session.execute(select(User).where(User.phone == phone))
    user = res.scalars().first()
    assert user is not None
    return user


async def _get_balance(async_client: AsyncClient, account_id: int, headers: dict[str, str]) -> Decimal:
    resp = await async_client.get(f"/api/v1/wallet/accounts/{account_id}/balance", headers=headers)
    assert resp.status_code == 200, resp.text
    return _q(resp.json()["balance"])


async def _ledger_signed_sum(async_db_session: AsyncSession, account_id: int) -> Decimal:
    await async_db_session.rollback()
    signed_amount = case(
        (wallet_ledger.c.entry_type.in_(["deposit", "transfer_in"]), wallet_ledger.c.amount),
        (wallet_ledger.c.entry_type.in_(["withdraw", "transfer_out"]), -wallet_ledger.c.amount),
        else_=Decimal("0"),
    )
    stmt = select(func.coalesce(func.sum(signed_amount), 0)).select_from(wallet_ledger).where(
        wallet_ledger.c.account_id == account_id
    )
    result = await async_db_session.execute(stmt)
    return _q(result.scalar_one())


async def assert_wallet_invariants(
    db: AsyncSession,
    account_id: int,
    *,
    async_client: AsyncClient,
    headers: dict[str, str],
) -> None:
    await db.rollback()
    balance = await _get_balance(async_client, account_id, headers)
    ledger_sum = await _ledger_signed_sum(db, account_id)
    assert balance == ledger_sum, f"balance={balance} ledger_sum={ledger_sum}"

    dup_stmt = (
        select(wallet_ledger.c.client_request_id, func.count())
        .where(wallet_ledger.c.account_id == account_id)
        .where(wallet_ledger.c.client_request_id.is_not(None))
        .group_by(wallet_ledger.c.client_request_id)
        .having(func.count() > 1)
    )
    dup_rows = (await db.execute(dup_stmt)).all()
    assert not dup_rows, f"duplicate client_request_id rows: {dup_rows}"

    adj_stmt = select(func.count()).select_from(wallet_ledger).where(
        wallet_ledger.c.account_id == account_id,
        wallet_ledger.c.entry_type == "adjustment",
    )
    adj_count = int((await db.execute(adj_stmt)).scalar_one())
    assert adj_count == 0, "adjustment entries are not supported by invariants"


@pytest.mark.asyncio
async def test_wallet_invariants_deposit(async_client: AsyncClient, async_db_session: AsyncSession, company_a_admin_headers):
    user_a = await _get_user(async_db_session, "+70000010001")

    acc_resp = await async_client.post(
        "/api/v1/wallet/accounts",
        json={"user_id": user_a.id, "currency": "KZT"},
        headers=company_a_admin_headers,
    )
    assert acc_resp.status_code == 201, acc_resp.text
    account_id = acc_resp.json()["id"]

    dep_id = str(uuid.uuid4())
    resp = await async_client.post(
        f"/api/v1/wallet/accounts/{account_id}/deposit",
        json={"amount": "100.00", "reference": "seed"},
        headers={**company_a_admin_headers, "X-Request-Id": dep_id},
    )
    assert resp.status_code == 200, resp.text

    await assert_wallet_invariants(async_db_session, account_id, async_client=async_client, headers=company_a_admin_headers)


@pytest.mark.asyncio
async def test_wallet_invariants_idempotent_deposit(
    async_client: AsyncClient, async_db_session: AsyncSession, company_a_admin_headers
):
    user_a = await _get_user(async_db_session, "+70000010001")

    acc_resp = await async_client.post(
        "/api/v1/wallet/accounts",
        json={"user_id": user_a.id, "currency": "KZT"},
        headers=company_a_admin_headers,
    )
    assert acc_resp.status_code == 201, acc_resp.text
    account_id = acc_resp.json()["id"]

    dep_id = str(uuid.uuid4())
    first = await async_client.post(
        f"/api/v1/wallet/accounts/{account_id}/deposit",
        json={"amount": "50.00", "reference": "seed"},
        headers={**company_a_admin_headers, "X-Request-Id": dep_id},
    )
    assert first.status_code == 200, first.text
    balance_after_first = await _get_balance(async_client, account_id, company_a_admin_headers)

    second = await async_client.post(
        f"/api/v1/wallet/accounts/{account_id}/deposit",
        json={"amount": "50.00", "reference": "seed"},
        headers={**company_a_admin_headers, "X-Request-Id": dep_id},
    )
    assert second.status_code == 200, second.text
    balance_after_second = await _get_balance(async_client, account_id, company_a_admin_headers)

    assert balance_after_second == balance_after_first
    await assert_wallet_invariants(async_db_session, account_id, async_client=async_client, headers=company_a_admin_headers)


@pytest.mark.asyncio
async def test_wallet_invariants_withdraw(async_client: AsyncClient, async_db_session: AsyncSession, company_a_admin_headers):
    user_a = await _get_user(async_db_session, "+70000010001")

    acc_resp = await async_client.post(
        "/api/v1/wallet/accounts",
        json={"user_id": user_a.id, "currency": "KZT"},
        headers=company_a_admin_headers,
    )
    assert acc_resp.status_code == 201, acc_resp.text
    account_id = acc_resp.json()["id"]

    seed = await async_client.post(
        f"/api/v1/wallet/accounts/{account_id}/deposit",
        json={"amount": "120.00", "reference": "seed"},
        headers=company_a_admin_headers,
    )
    assert seed.status_code == 200, seed.text

    wd_id = str(uuid.uuid4())
    withdraw = await async_client.post(
        f"/api/v1/wallet/accounts/{account_id}/withdraw",
        json={"amount": "30.00", "reference": "wd"},
        headers={**company_a_admin_headers, "X-Request-Id": wd_id},
    )
    if withdraw.status_code == 404:
        pytest.skip("withdraw endpoint not available")
    assert withdraw.status_code == 200, withdraw.text

    await assert_wallet_invariants(async_db_session, account_id, async_client=async_client, headers=company_a_admin_headers)


@pytest.mark.asyncio
async def test_wallet_invariants_payments_refund(
    async_client: AsyncClient, async_db_session: AsyncSession, company_a_admin_headers
):
    health = await async_client.get("/api/v1/payments/health")
    if health.status_code == 404:
        pytest.skip("payments flow not available")

    user_a = await _get_user(async_db_session, "+70000010001")

    acc_resp = await async_client.post(
        "/api/v1/wallet/accounts",
        json={"user_id": user_a.id, "currency": "KZT"},
        headers=company_a_admin_headers,
    )
    assert acc_resp.status_code == 201, acc_resp.text
    account_id = acc_resp.json()["id"]

    seed = await async_client.post(
        f"/api/v1/wallet/accounts/{account_id}/deposit",
        json={"amount": "200.00", "reference": "seed"},
        headers=company_a_admin_headers,
    )
    assert seed.status_code == 200, seed.text

    payment = await async_client.post(
        "/api/v1/payments/",
        json={
            "user_id": user_a.id,
            "wallet_account_id": account_id,
            "amount": "40.00",
            "currency": "KZT",
            "reference": "inv-1",
        },
        headers=company_a_admin_headers,
    )
    assert payment.status_code == 201, payment.text
    payment_id = payment.json()["id"]

    await assert_wallet_invariants(async_db_session, account_id, async_client=async_client, headers=company_a_admin_headers)

    refund = await async_client.post(
        f"/api/v1/payments/{payment_id}/refund",
        json={"amount": "40.00", "reference": "refund"},
        headers=company_a_admin_headers,
    )
    assert refund.status_code == 200, refund.text

    await assert_wallet_invariants(async_db_session, account_id, async_client=async_client, headers=company_a_admin_headers)
