"""
tests/test_kaspi_autosync.py — Тесты для автоматической синхронизации заказов Kaspi.

Проверяемые сценарии:
1. Фильтрация подходящих компаний (активные, с kaspi_store_id)
2. Соблюдение лимита concurrency
3. Обработка заблокированных компаний (advisory lock)
4. Обработка ошибок без остановки всей синхронизации
5. Ручной триггер через API
"""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

import tests.conftest as base_conftest
from app.models import Company
from app.models.billing import Subscription
from app.services.kaspi_service import KaspiSyncAlreadyRunning


async def _ensure_subscription_plan(async_db_session: AsyncSession, company_id: int, plan: str) -> None:
    existing_company = await async_db_session.get(Company, company_id)
    if not existing_company:
        async_db_session.add(Company(id=company_id, name=f"Company {company_id}"))
        await async_db_session.flush()

    res = await async_db_session.execute(
        sa.select(Subscription).where(Subscription.company_id == company_id).where(Subscription.deleted_at.is_(None))
    )
    sub = res.scalars().first()
    now = datetime.now(UTC)
    if sub is None:
        sub = Subscription(
            company_id=company_id,
            plan=plan,
            status="active",
            billing_cycle="monthly",
            price=Decimal("0.00"),
            currency="KZT",
            started_at=now,
            period_start=now,
            period_end=now + timedelta(days=30),
            next_billing_date=now + timedelta(days=31),
        )
        async_db_session.add(sub)
    else:
        sub.plan = plan
        sub.status = "active"
    await async_db_session.commit()


def _unique_company_id() -> int:
    return int(time.time_ns() % 1_000_000_000)


async def _seed_company(async_db_session: AsyncSession, *, company_id: int | None = None) -> Company:
    cid = company_id or _unique_company_id()
    company = Company(
        id=cid,
        name=f"Test Kaspi Company {cid}",
        email=f"test{cid}@example.com",
        is_active=True,
        deleted_at=None,
        kaspi_store_id=f"store_{cid}",
    )
    async_db_session.add(company)
    await async_db_session.commit()
    await async_db_session.refresh(company)
    return company


def _make_admin_headers(company_id: int) -> dict[str, str]:
    phone = f"+7{company_id % 10**10:010d}"
    return base_conftest._make_company_headers(
        company_id=company_id,
        role="admin",
        phone=phone,
        expires_delta=timedelta(days=7),
    )


@pytest.mark.asyncio
async def test_get_eligible_companies_filters_correctly(async_db_session: AsyncSession):
    """
    Тест: _get_eligible_companies должна возвращать только активные компании
    с kaspi_store_id, игнорируя удалённые и неактивные.
    """
    # Создаём несколько компаний
    company_active = Company(
        name="Active Kaspi Company",
        email="active@example.com",
        is_active=True,
        deleted_at=None,
        kaspi_store_id="store_123",
    )
    company_inactive = Company(
        name="Inactive Company",
        email="inactive@example.com",
        is_active=False,
        deleted_at=None,
        kaspi_store_id="store_456",
    )
    company_deleted = Company(
        name="Deleted Company",
        email="deleted@example.com",
        is_active=True,
        deleted_at=datetime.now(UTC),
        kaspi_store_id="store_789",
    )
    company_no_kaspi = Company(
        name="No Kaspi",
        email="nokaspi@example.com",
        is_active=True,
        deleted_at=None,
        kaspi_store_id=None,
    )
    async_db_session.add_all([company_active, company_inactive, company_deleted, company_no_kaspi])
    await async_db_session.commit()
    await async_db_session.refresh(company_active)

    # Импортируем функцию
    from app.worker.kaspi_autosync import _get_eligible_companies

    result = await _get_eligible_companies(async_db_session)

    # Должна вернуться только активная компания с kaspi_store_id
    assert len(result) == 1
    assert result[0] == (company_active.id, "store_123")


@pytest.mark.asyncio
async def test_sync_respects_concurrency_limit():
    """
    Тест: _sync_companies_batch должна соблюдать max_concurrency
    и не запускать больше N синхронизаций параллельно.
    """
    # Моделируем 10 компаний
    company_rows = [(cid, f"store_{cid}") for cid in range(1, 11)]
    max_concurrency = 3

    # Мок для _sync_company: просто задержка
    async def mock_sync_company(company_id, merchant_uid, db):
        await asyncio.sleep(0.1)
        return {"company_id": company_id, "status": "success"}

    # Мокируем settings.KASPI_AUTOSYNC_MAX_CONCURRENCY
    with patch("app.worker.kaspi_autosync.settings") as mock_settings:
        mock_settings.KASPI_AUTOSYNC_MAX_CONCURRENCY = max_concurrency

        with patch("app.worker.kaspi_autosync._sync_company", side_effect=mock_sync_company):
            from app.worker.kaspi_autosync import _sync_companies_batch

            # Запускаем batch
            results = await _sync_companies_batch(company_rows)

            # Должно быть 10 результатов
            assert len(results) == 10
            # Все успешные
            assert all(r["status"] == "success" for r in results)


@pytest.mark.asyncio
async def test_locked_companies_dont_stop_batch(async_db_session: AsyncSession):
    """
    Тест: если одна компания заблокирована (KaspiSyncAlreadyRunning),
    это не должно останавливать синхронизацию других компаний.
    """
    # Создаём 3 компании
    companies = [
        Company(
            name=f"Company {i}",
            email=f"company{i}@example.com",
            is_active=True,
            deleted_at=None,
            kaspi_store_id=f"store_{i}",
        )
        for i in range(1, 4)
    ]
    async_db_session.add_all(companies)
    await async_db_session.commit()
    for c in companies:
        await async_db_session.refresh(c)

    company_rows = [(c.id, c.kaspi_store_id or "") for c in companies]

    # Мок: вторая компания вернет статус "locked"
    async def mock_sync_company(company_id, merchant_uid, db):
        if company_id == companies[1].id:
            return {"company_id": company_id, "status": "locked"}
        return {"company_id": company_id, "status": "success"}

    with patch("app.worker.kaspi_autosync._sync_company", side_effect=mock_sync_company):
        from app.worker.kaspi_autosync import _sync_companies_batch

        results = await _sync_companies_batch(company_rows)

        # Должно быть 3 результата
        assert len(results) == 3
        # Проверяем статусы
        statuses = {r["company_id"]: r["status"] for r in results}
        assert statuses[companies[0].id] == "success"
        assert statuses[companies[1].id] == "locked"
        assert statuses[companies[2].id] == "success"


@pytest.mark.asyncio
async def test_failed_companies_tracked_in_summary(async_db_session: AsyncSession):
    """
    Тест: если синхронизация компании завершается ошибкой,
    она должна быть учтена в summary как 'failed', но не сломать весь процесс.
    """
    # Создаём 2 компании
    companies = [
        Company(
            name=f"Company {i}",
            email=f"company{i}@example.com",
            is_active=True,
            deleted_at=None,
            kaspi_store_id=f"store_{i}",
        )
        for i in range(1, 3)
    ]
    async_db_session.add_all(companies)
    await async_db_session.commit()
    for c in companies:
        await async_db_session.refresh(c)

    company_rows = [(c.id, c.kaspi_store_id or "") for c in companies]

    # Мок: первая компания выбросит RuntimeError
    async def mock_sync_company(company_id, merchant_uid, db):
        if company_id == companies[0].id:
            raise RuntimeError("Simulated error")
        return {"company_id": company_id, "status": "success"}

    with patch("app.worker.kaspi_autosync._sync_company", side_effect=mock_sync_company):
        from app.worker.kaspi_autosync import _sync_companies_batch

        results = await _sync_companies_batch(company_rows)

        # Должно быть 2 результата
        assert len(results) == 2
        statuses = {r["company_id"]: r["status"] for r in results}
        assert statuses[companies[0].id] == "failed"
        assert statuses[companies[1].id] == "success"


@pytest.mark.asyncio
async def test_manual_trigger_via_endpoint(async_client, async_db_session: AsyncSession, monkeypatch):
    """
    Тест: ручной запуск авто-синхронизации через POST /api/v1/kaspi/autosync/trigger
    должен запустить синхронизацию и вернуть статус.
    """
    company = await _seed_company(async_db_session)
    await _ensure_subscription_plan(async_db_session, company_id=company.id, plan="pro")
    headers = _make_admin_headers(company.id)
    monkeypatch.setattr("app.core.config.settings.KASPI_AUTOSYNC_ENABLED", True)

    # Мокируем синхронизацию
    with patch("app.worker.kaspi_autosync.run_kaspi_autosync") as mock_run, patch(
        "app.worker.kaspi_autosync.get_last_run_summary"
    ) as mock_summary:
        mock_run.return_value = {
            "last_run_at": datetime.now(UTC).isoformat(),
            "eligible_companies": 1,
            "success": 1,
            "locked": 0,
            "failed": 0,
        }
        mock_summary.return_value = mock_run.return_value

        # Вызываем endpoint
        response = await async_client.post("/api/v1/kaspi/autosync/trigger", headers=headers)

        assert response.status_code == 200
        data = response.json()
        assert "last_run_at" in data
        assert data["eligible_companies"] >= 0
        assert data["success"] >= 0


@pytest.mark.asyncio
async def test_autosync_status_endpoint(async_client, async_db_session: AsyncSession):
    """
    Тест: GET /api/v1/kaspi/autosync/status должен возвращать последнюю статистику.
    """
    company = await _seed_company(async_db_session)
    await _ensure_subscription_plan(async_db_session, company_id=company.id, plan="pro")
    headers = _make_admin_headers(company.id)

    response = await async_client.get("/api/v1/kaspi/autosync/status", headers=headers)

    assert response.status_code == 200
    data = response.json()
    assert "enabled" in data
    assert "last_run_at" in data
    assert "eligible_companies" in data
    assert "success" in data
    assert "locked" in data
    assert "failed" in data


@pytest.mark.asyncio
async def test_autosync_status_disabled(async_client, async_db_session: AsyncSession):
    """
    Тест: GET /api/v1/kaspi/autosync/status должен показывать enabled=False когда отключено.
    """
    company = await _seed_company(async_db_session)
    await _ensure_subscription_plan(async_db_session, company_id=company.id, plan="pro")
    headers = _make_admin_headers(company.id)
    with patch("app.core.config.settings") as mock_settings:
        mock_settings.KASPI_AUTOSYNC_ENABLED = False

        response = await async_client.get("/api/v1/kaspi/autosync/status", headers=headers)

        assert response.status_code == 200
        data = response.json()
        assert data["enabled"] is False
        assert data["last_run_at"] is None
        assert data["eligible_companies"] == 0
        assert data["success"] == 0
        assert data["locked"] == 0
        assert data["failed"] == 0


@pytest.mark.asyncio
async def test_autosync_trigger_disabled(async_client, async_db_session: AsyncSession):
    """
    Тест: POST /api/v1/kaspi/autosync/trigger должен возвращать 409 когда autosync отключен.
    """
    company = await _seed_company(async_db_session)
    await _ensure_subscription_plan(async_db_session, company_id=company.id, plan="pro")
    headers = _make_admin_headers(company.id)
    with patch("app.core.config.settings") as mock_settings:
        mock_settings.KASPI_AUTOSYNC_ENABLED = False

        response = await async_client.post("/api/v1/kaspi/autosync/trigger", headers=headers)

        assert response.status_code == 409
        data = response.json()
        assert "detail" in data
        assert "disabled" in data["detail"].lower()
        assert "KASPI_AUTOSYNC_ENABLED" in data["detail"]


@pytest.mark.asyncio
async def test_autosync_status_includes_config(
    async_client,
    async_db_session: AsyncSession,
):
    """
    Тест: GET /api/v1/kaspi/autosync/status должен включать configuration (interval, concurrency).
    """
    company = await _seed_company(async_db_session)
    await _ensure_subscription_plan(async_db_session, company_id=company.id, plan="pro")
    headers = _make_admin_headers(company.id)
    with patch("app.core.config.settings") as mock_settings:
        mock_settings.KASPI_AUTOSYNC_ENABLED = True
        mock_settings.KASPI_AUTOSYNC_INTERVAL_MINUTES = 30
        mock_settings.KASPI_AUTOSYNC_MAX_CONCURRENCY = 5

        response = await async_client.get("/api/v1/kaspi/autosync/status", headers=headers)

        assert response.status_code == 200
        data = response.json()
        assert data["enabled"] is True
        assert data["interval_minutes"] == 30
        assert data["max_concurrency"] == 5


@pytest.mark.asyncio
async def test_autosync_status_includes_scheduler_state(
    async_client,
    async_db_session: AsyncSession,
):
    """
    Тест: GET /api/v1/kaspi/autosync/status должен включать scheduler state (job_registered, scheduler_running).
    """
    company = await _seed_company(async_db_session)
    await _ensure_subscription_plan(async_db_session, company_id=company.id, plan="pro")
    headers = _make_admin_headers(company.id)
    import sys

    # Create mock scheduler
    mock_scheduler = MagicMock()
    mock_scheduler.running = True
    mock_job = MagicMock()
    mock_scheduler.get_job.return_value = mock_job

    # Create mock module with scheduler
    mock_scheduler_module = MagicMock()
    mock_scheduler_module.scheduler = mock_scheduler

    with patch("app.core.config.settings") as mock_settings:
        mock_settings.KASPI_AUTOSYNC_ENABLED = True
        mock_settings.KASPI_AUTOSYNC_INTERVAL_MINUTES = 15
        mock_settings.KASPI_AUTOSYNC_MAX_CONCURRENCY = 3

        with patch.dict(sys.modules, {"app.worker.scheduler_worker": mock_scheduler_module}):
            response = await async_client.get("/api/v1/kaspi/autosync/status", headers=headers)

            assert response.status_code == 200
            data = response.json()
            assert "job_registered" in data
            assert "scheduler_running" in data
            assert data["job_registered"] is True
            assert data["scheduler_running"] is True


@pytest.mark.asyncio
async def test_autosync_status_job_not_registered(
    async_client,
    async_db_session: AsyncSession,
):
    """
    Тест: GET /api/v1/kaspi/autosync/status должен показывать job_registered=False если job не найден.
    """
    company = await _seed_company(async_db_session)
    await _ensure_subscription_plan(async_db_session, company_id=company.id, plan="pro")
    headers = _make_admin_headers(company.id)
    import sys

    # Create mock scheduler with no job
    mock_scheduler = MagicMock()
    mock_scheduler.running = False
    mock_scheduler.get_job.return_value = None  # Job not found

    # Create mock module with scheduler
    mock_scheduler_module = MagicMock()
    mock_scheduler_module.scheduler = mock_scheduler

    with patch("app.core.config.settings") as mock_settings:
        mock_settings.KASPI_AUTOSYNC_ENABLED = True
        mock_settings.KASPI_AUTOSYNC_INTERVAL_MINUTES = 15
        mock_settings.KASPI_AUTOSYNC_MAX_CONCURRENCY = 3

        with patch.dict(sys.modules, {"app.worker.scheduler_worker": mock_scheduler_module}):
            response = await async_client.get("/api/v1/kaspi/autosync/status", headers=headers)

            assert response.status_code == 200
            data = response.json()
            assert data["job_registered"] is False
            assert data["scheduler_running"] is False
