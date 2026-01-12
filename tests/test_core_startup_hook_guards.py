import os

import pytest
from fastapi import FastAPI

import app.core.__init__ as core
from app.core import init_core


@pytest.mark.asyncio
async def test_startup_hooks_disabled(monkeypatch):
    """Startup hooks are skipped entirely in tests/CI mode."""

    def _should_not_run():
        raise AssertionError("should not run")

    monkeypatch.setattr(core, "should_disable_startup_hooks", lambda: True)
    monkeypatch.setattr(core, "_run_alembic_migrations_if_needed", _should_not_run)

    app = FastAPI()
    init_core(app)

    await app.router.startup()  # Should not raise


@pytest.mark.asyncio
async def test_startup_skipped_for_non_web_role(monkeypatch):
    """Startup hooks are skipped for non-web roles (e.g., scheduler)."""

    def _should_not_run():
        raise AssertionError("should not run")

    monkeypatch.setattr(core, "should_disable_startup_hooks", lambda: False)
    monkeypatch.setattr(core.settings, "PROCESS_ROLE", "scheduler")
    monkeypatch.setattr(core, "_run_alembic_migrations_if_needed", _should_not_run)

    app = FastAPI()
    init_core(app)

    await app.router.startup()  # Should not raise


@pytest.mark.asyncio
async def test_startup_web_role_respects_migration_flag(monkeypatch):
    """Web role runs startup hook but respects RUN_MIGRATIONS_ON_START flag."""

    monkeypatch.setattr(core, "should_disable_startup_hooks", lambda: False)
    monkeypatch.setattr(core.settings, "PROCESS_ROLE", "web")
    monkeypatch.delenv("RUN_MIGRATIONS_ON_START", raising=False)

    calls = {"count": 0}

    def _record_if_enabled():
        if os.getenv("RUN_MIGRATIONS_ON_START", "0") in ("1", "true", "True"):
            calls["count"] += 1

    monkeypatch.setattr(core, "_run_alembic_migrations_if_needed", _record_if_enabled)

    app = FastAPI()
    init_core(app)

    await app.router.startup()

    # Since RUN_MIGRATIONS_ON_START is unset/false, migration callable should not increment
    assert calls["count"] == 0
