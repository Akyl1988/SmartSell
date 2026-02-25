from __future__ import annotations

import csv
from datetime import UTC, datetime, timedelta
from io import StringIO

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

import tests.conftest as base_conftest
from app.models.billing import Subscription
from app.models.company import Company
from app.models.repricing import RepricingRun

pytestmark = pytest.mark.asyncio


def _seed_repricing_run(
    *,
    company_id: int,
    status: str,
    created_at: datetime,
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

        run = RepricingRun(
            company_id=company_id,
            status=status,
            created_at=created_at,
            started_at=created_at,
            finished_at=created_at + timedelta(minutes=3),
            processed=10,
            changed=4,
            failed=1,
            error_code="",
            error_message="",
            request_id="req-1",
        )
        s.add(run)
        s.commit()
        s.refresh(run)
        return int(run.id)


def _parse_csv(text: str) -> list[list[str]]:
    buf = StringIO(text)
    return list(csv.reader(buf))


async def test_repricing_runs_csv_ok_for_admin(async_client, company_a_admin_headers, test_db):
    _ = test_db
    now = datetime.now(UTC)
    run_id = _seed_repricing_run(company_id=1001, status="completed", created_at=now)

    resp = await async_client.get(
        "/api/v1/reports/repricing_runs.csv?limit=1",
        headers=company_a_admin_headers,
    )
    assert resp.status_code == 200, resp.text
    assert "text/csv" in resp.headers.get("content-type", "")

    rows = _parse_csv(resp.text)
    assert rows
    assert rows[0] == [
        "company_id",
        "run_id",
        "rule_id",
        "status",
        "created_at",
        "started_at",
        "finished_at",
        "processed_count",
        "changed_count",
        "failed_count",
        "error_code",
        "error_message",
        "request_id",
    ]
    assert len(rows) >= 2
    header = rows[0]
    row = rows[1]
    values = dict(zip(header, row, strict=True))
    assert values["run_id"] == str(run_id)


@pytest.mark.no_subscription
async def test_repricing_runs_csv_subscription_required(async_client, company_a_admin_headers):
    resp = await async_client.get(
        "/api/v1/reports/repricing_runs.csv",
        headers=company_a_admin_headers,
    )
    assert resp.status_code == 402, resp.text
    detail = resp.json().get("detail")
    assert isinstance(detail, dict)
    assert detail.get("code") == "SUBSCRIPTION_REQUIRED"


async def test_repricing_runs_csv_feature_required(async_client, async_db_session, company_a_admin_headers):
    sub = (
        (
            await async_db_session.execute(
                select(Subscription).where(
                    Subscription.company_id == 1001,
                    Subscription.deleted_at.is_(None),
                )
            )
        )
        .scalars()
        .first()
    )
    assert sub is not None
    sub.plan = "basic"
    sub.status = "active"
    await async_db_session.commit()

    resp = await async_client.get(
        "/api/v1/reports/repricing_runs.csv",
        headers=company_a_admin_headers,
    )
    assert resp.status_code == 402, resp.text
    detail = resp.json().get("detail")
    assert isinstance(detail, dict)
    assert detail.get("code") == "SUBSCRIPTION_REQUIRED"


async def test_repricing_runs_csv_rbac(async_client, company_a_employee_headers):
    resp = await async_client.get(
        "/api/v1/reports/repricing_runs.csv",
        headers=company_a_employee_headers,
    )
    assert resp.status_code == 403, resp.text
    payload = resp.json()
    assert payload.get("code") == "ADMIN_REQUIRED"
