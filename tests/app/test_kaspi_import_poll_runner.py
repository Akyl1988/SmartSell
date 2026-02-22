import pytest

from app.models.company import Company
from app.models.kaspi_import_run import KaspiImportRun
from app.models.marketplace import KaspiStoreToken
from app.worker import kaspi_import_poll
from app.worker.kaspi_import_poll import run_kaspi_import_poll_async


@pytest.mark.asyncio
async def test_kaspi_import_poll_runner_updates_status(async_db_session, monkeypatch):
    company = await async_db_session.get(Company, 1001)
    if not company:
        company = Company(id=1001, name="Company 1001", kaspi_store_id="store-a")
        async_db_session.add(company)
    else:
        company.kaspi_store_id = "store-a"
    await async_db_session.commit()

    run = KaspiImportRun(
        company_id=1001,
        merchant_uid="store-a",
        import_code="RUN-1",
        kaspi_import_code="IC-1",
        status="UPLOADED",
        request_payload=[],
    )
    async_db_session.add(run)
    await async_db_session.commit()
    await async_db_session.refresh(run)

    async def _get_token(session, store_name: str):  # noqa: ARG001
        return "token-a"

    monkeypatch.setattr(KaspiStoreToken, "get_token", _get_token)

    from app.services.kaspi_goods_import_client import KaspiGoodsImportClient

    async def _get_status(self, import_code: str):  # noqa: ANN001
        assert import_code == "IC-1"
        return {"status": "FINISHED_OK"}

    async def _get_result(self, import_code: str):  # noqa: ANN001
        assert import_code == "IC-1"
        return {"status": "FINISHED_OK", "ok": True}

    monkeypatch.setattr(KaspiGoodsImportClient, "get_status", _get_status)
    monkeypatch.setattr(KaspiGoodsImportClient, "get_result", _get_result)

    summary = await run_kaspi_import_poll_async()
    assert summary["polled"] >= 1

    await async_db_session.refresh(run)
    assert run.status == "FINISHED_OK"
    assert run.result_json
    assert run.next_poll_at is None
    assert run.error_code is None


@pytest.mark.asyncio
async def test_kaspi_import_poll_runner_backoff_on_error(async_db_session, monkeypatch):
    company = await async_db_session.get(Company, 1001)
    if not company:
        company = Company(id=1001, name="Company 1001", kaspi_store_id="store-a")
        async_db_session.add(company)
    else:
        company.kaspi_store_id = "store-a"
    await async_db_session.commit()

    run = KaspiImportRun(
        company_id=1001,
        merchant_uid="store-a",
        import_code="RUN-2",
        kaspi_import_code="IC-2",
        status="UPLOADED",
        request_payload=[],
    )
    async_db_session.add(run)
    await async_db_session.commit()
    await async_db_session.refresh(run)

    async def _get_token(session, store_name: str):  # noqa: ARG001
        return "token-a"

    monkeypatch.setattr(KaspiStoreToken, "get_token", _get_token)

    from app.services.kaspi_goods_import_client import KaspiGoodsImportClient, KaspiImportUpstreamUnavailable

    async def _get_status(self, import_code: str):  # noqa: ANN001
        assert import_code == "IC-2"
        raise KaspiImportUpstreamUnavailable("kaspi_upstream_unavailable")

    monkeypatch.setattr(KaspiGoodsImportClient, "get_status", _get_status)

    summary = await run_kaspi_import_poll_async()
    assert summary["failed"] >= 1

    await async_db_session.refresh(run)
    assert run.error_code == "upstream_unavailable"
    assert run.next_poll_at is not None


def test_kaspi_import_poll_job_importable(monkeypatch):
    async def _fake_async():
        return {"polled": 0, "failed": 0, "skipped": 0, "locked": 1}

    monkeypatch.setattr(kaspi_import_poll, "run_kaspi_import_poll_async", _fake_async)
    result = kaspi_import_poll.run_kaspi_import_poll()
    assert result.get("locked") == 1
