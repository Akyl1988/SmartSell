from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models.repricing import RepricingRule, RepricingRun
from app.worker import scheduler_worker

pytestmark = pytest.mark.asyncio


def _rule_payload(company_id: int) -> RepricingRule:
    return RepricingRule(
        company_id=company_id,
        name="auto-rule",
        enabled=True,
        is_active=True,
        scope_type="all",
        step=Decimal("5.00"),
        rounding_mode="nearest",
    )


async def test_repricing_autorun_skips_when_disabled(
    monkeypatch,
    test_db,
    async_db_session,
    factory,
):
    _ = test_db
    monkeypatch.setenv("ENABLE_SCHEDULER", "1")
    monkeypatch.setenv("REPRICING_AUTORUN_ENABLED", "0")

    company = await factory["create_company"]()
    async_db_session.add(_rule_payload(company.id))
    await async_db_session.commit()

    result = await scheduler_worker.run_repricing_autorun_job_async()
    assert result is None

    runs = (
        (await async_db_session.execute(select(RepricingRun).where(RepricingRun.company_id == company.id)))
        .scalars()
        .all()
    )
    assert not runs


async def test_repricing_autorun_runs_when_enabled(
    monkeypatch,
    test_db,
    async_db_session,
    factory,
):
    _ = test_db
    monkeypatch.setenv("ENABLE_SCHEDULER", "1")
    monkeypatch.setenv("REPRICING_AUTORUN_ENABLED", "1")

    company = await factory["create_company"]()
    async_db_session.add(_rule_payload(company.id))
    await async_db_session.commit()

    result = await scheduler_worker.run_repricing_autorun_job_async()
    assert result is not None
    assert result.get("eligible") == 1
    assert result.get("processed") == 1

    runs = (
        (await async_db_session.execute(select(RepricingRun).where(RepricingRun.company_id == company.id)))
        .scalars()
        .all()
    )
    assert len(runs) == 1


async def test_repricing_autorun_respects_cooldown(
    monkeypatch,
    test_db,
    async_db_session,
    factory,
):
    _ = test_db
    monkeypatch.setenv("ENABLE_SCHEDULER", "1")
    monkeypatch.setenv("REPRICING_AUTORUN_ENABLED", "1")

    company = await factory["create_company"]()
    rule = _rule_payload(company.id)
    rule.cooldown_seconds = 3600
    async_db_session.add(rule)
    await async_db_session.commit()

    run = RepricingRun(
        company_id=company.id,
        status="done",
        started_at=datetime.utcnow() - timedelta(minutes=5),
        finished_at=datetime.utcnow(),
    )
    async_db_session.add(run)
    await async_db_session.commit()

    result = await scheduler_worker.run_repricing_autorun_job_async()
    assert result is not None
    assert result.get("skipped") == 1

    runs = (
        (await async_db_session.execute(select(RepricingRun).where(RepricingRun.company_id == company.id)))
        .scalars()
        .all()
    )
    assert len(runs) == 1
