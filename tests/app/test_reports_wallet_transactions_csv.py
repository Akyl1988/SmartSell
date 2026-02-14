from __future__ import annotations

import csv
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from io import StringIO

import pytest
from sqlalchemy.orm import sessionmaker

import tests.conftest as base_conftest
from app.models.billing import WalletBalance, WalletTransaction
from app.models.company import Company

pytestmark = pytest.mark.asyncio


def _seed_wallet_tx(
    *,
    company_id: int,
    created_at: datetime,
    amount: Decimal,
    balance_after: Decimal,
    currency: str = "KZT",
    transaction_type: str = "credit",
    reference_type: str = "manual_topup",
    reference_id: int | None = 1,
) -> int:
    if base_conftest.sync_engine is None:
        raise RuntimeError("sync_engine is not initialized; ensure test_db fixture runs first")

    SessionLocal = sessionmaker(bind=base_conftest.sync_engine, expire_on_commit=False, autoflush=False)
    with SessionLocal() as s:
        company = s.get(Company, company_id)
        if company is None:
            company = Company(id=company_id, name=f"Company {company_id}")
            s.add(company)
            s.flush()

        wallet = s.query(WalletBalance).filter(WalletBalance.company_id == company_id).first()
        if wallet is None:
            wallet = WalletBalance(company_id=company_id, balance=Decimal("0"), currency=currency)
            s.add(wallet)
            s.flush()

        balance_before = balance_after - amount if transaction_type == "credit" else balance_after + amount
        trx = WalletTransaction(
            wallet_id=wallet.id,
            transaction_type=transaction_type,
            amount=amount,
            balance_before=balance_before,
            balance_after=balance_after,
            reference_type=reference_type,
            reference_id=reference_id,
            description="report seed",
            created_at=created_at,
        )
        s.add(trx)
        s.commit()
        s.refresh(trx)
        return int(trx.id)


def _parse_csv(text: str) -> list[list[str]]:
    buf = StringIO(text)
    return list(csv.reader(buf))


async def test_wallet_transactions_csv_store_admin(async_client, company_a_admin_headers, test_db):
    _ = test_db
    now = datetime.now(UTC)
    _seed_wallet_tx(
        company_id=1001,
        created_at=now - timedelta(days=1),
        amount=Decimal("10.00"),
        balance_after=Decimal("110.00"),
    )
    _seed_wallet_tx(
        company_id=1001,
        created_at=now,
        amount=Decimal("5.00"),
        balance_after=Decimal("115.00"),
        reference_id=2,
    )

    resp = await async_client.get(
        "/api/v1/reports/wallet/transactions.csv?limit=1",
        headers=company_a_admin_headers,
    )
    assert resp.status_code == 200, resp.text
    assert "text/csv" in resp.headers.get("content-type", "")

    rows = _parse_csv(resp.text)
    assert rows
    assert rows[0] == [
        "transaction_id",
        "created_at",
        "amount",
        "currency",
        "type",
        "reference",
        "balance_after",
    ]
    assert len(rows) <= 2


async def test_wallet_transactions_csv_employee_denied(async_client, company_a_employee_headers, test_db):
    _ = test_db
    resp = await async_client.get(
        "/api/v1/reports/wallet/transactions.csv",
        headers=company_a_employee_headers,
    )
    assert resp.status_code == 403, resp.text
    payload = resp.json()
    assert payload.get("code") == "ADMIN_REQUIRED"


async def test_wallet_transactions_csv_tenant_isolation(async_client, company_a_admin_headers, test_db):
    _ = test_db
    now = datetime.now(UTC)
    _seed_wallet_tx(
        company_id=2001,
        created_at=now,
        amount=Decimal("7.00"),
        balance_after=Decimal("207.00"),
    )

    resp = await async_client.get(
        "/api/v1/reports/wallet/transactions.csv?company_id=2001",
        headers=company_a_admin_headers,
    )
    assert resp.status_code == 404, resp.text


async def test_wallet_transactions_csv_platform_admin(async_client, auth_headers, test_db):
    _ = test_db
    now = datetime.now(UTC)
    _seed_wallet_tx(
        company_id=1001,
        created_at=now,
        amount=Decimal("12.00"),
        balance_after=Decimal("112.00"),
        reference_id=3,
    )

    resp = await async_client.get(
        "/api/v1/reports/wallet/transactions.csv?company_id=1001&limit=10",
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    rows = _parse_csv(resp.text)
    assert rows
