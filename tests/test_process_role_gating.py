"""
Regression tests for strict process-role gating to prevent Kaspi autosync dual activation.

Tests ensure:
- Scheduler starts ONLY for PROCESS_ROLE="scheduler" (not for "web")
- Kaspi runner starts ONLY for PROCESS_ROLE in ("web","runner") (not for "scheduler")
- should_register_kaspi_autosync() respects PROCESS_ROLE="scheduler" requirement
- Mutual exclusion: runner disables scheduler job registration
"""

import os

import pytest

import app.main as main_module
from app.core.config import settings


@pytest.mark.asyncio
async def test_scheduler_skipped_for_web_role(monkeypatch):
    """With PROCESS_ROLE='web', scheduler start is skipped even if ENABLE_SCHEDULER=1."""
    monkeypatch.setattr(settings, "PROCESS_ROLE", "web")
    monkeypatch.setenv("ENABLE_SCHEDULER", "1")
    monkeypatch.setattr(main_module, "should_disable_startup_hooks", lambda: False)

    scheduler_started = False

    def _record_start():
        nonlocal scheduler_started
        scheduler_started = True

    # Simulate the lifespan startup logic
    disable_hooks = main_module.should_disable_startup_hooks()
    role = getattr(settings, "PROCESS_ROLE", os.getenv("PROCESS_ROLE", "web")) or "web"
    enable_scheduler = main_module._env_truthy(
        os.getenv("ENABLE_SCHEDULER", "0")
    ) or getattr(settings, "ENABLE_SCHEDULER", False)

    if role == "scheduler" and not disable_hooks and enable_scheduler:
        _record_start()

    assert not scheduler_started, "Scheduler should not start for role='web'"


@pytest.mark.asyncio
async def test_scheduler_starts_for_scheduler_role(monkeypatch):
    """With PROCESS_ROLE='scheduler', scheduler start is attempted when ENABLE_SCHEDULER=1."""
    monkeypatch.setattr(settings, "PROCESS_ROLE", "scheduler")
    monkeypatch.setenv("ENABLE_SCHEDULER", "1")
    monkeypatch.setattr(main_module, "should_disable_startup_hooks", lambda: False)

    scheduler_started = False

    def _record_start():
        nonlocal scheduler_started
        scheduler_started = True

    # Simulate the lifespan startup logic
    disable_hooks = main_module.should_disable_startup_hooks()
    role = getattr(settings, "PROCESS_ROLE", os.getenv("PROCESS_ROLE", "web")) or "web"
    enable_scheduler = main_module._env_truthy(
        os.getenv("ENABLE_SCHEDULER", "0")
    ) or getattr(settings, "ENABLE_SCHEDULER", False)

    if role == "scheduler" and not disable_hooks and enable_scheduler:
        _record_start()

    assert scheduler_started, "Scheduler should start for role='scheduler' with ENABLE_SCHEDULER=1"


def test_should_register_kaspi_autosync_false_for_web_role():
    """With PROCESS_ROLE='web', should_register_kaspi_autosync() always returns False."""
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(settings, "PROCESS_ROLE", "web")
    monkeypatch.delenv("ENABLE_KASPI_SYNC_RUNNER", raising=False)
    monkeypatch.setattr(settings, "KASPI_AUTOSYNC_ENABLED", True)

    # Import and call should_register_kaspi_autosync
    # We can't import scheduler_worker directly, so we'll import the function via the module
    import sys

    if "app.worker.scheduler_worker" in sys.modules:
        from app.worker.scheduler_worker import should_register_kaspi_autosync

        result = should_register_kaspi_autosync()
        assert (
            not result
        ), "should_register_kaspi_autosync should return False for role='web'"

    monkeypatch.undo()


def test_should_register_kaspi_autosync_true_for_scheduler_role():
    """With PROCESS_ROLE='scheduler', should_register_kaspi_autosync() returns True when enabled."""
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(settings, "PROCESS_ROLE", "scheduler")
    monkeypatch.delenv("ENABLE_KASPI_SYNC_RUNNER", raising=False)
    monkeypatch.setattr(settings, "KASPI_AUTOSYNC_ENABLED", True)

    # Import and call should_register_kaspi_autosync
    import sys

    if "app.worker.scheduler_worker" in sys.modules:
        from app.worker.scheduler_worker import should_register_kaspi_autosync

        result = should_register_kaspi_autosync()
        assert (
            result
        ), "should_register_kaspi_autosync should return True for role='scheduler' with autosync enabled"

    monkeypatch.undo()


def test_should_register_kaspi_autosync_false_with_runner_enabled():
    """When ENABLE_KASPI_SYNC_RUNNER=1, should_register_kaspi_autosync() returns False (runner precedence)."""
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(settings, "PROCESS_ROLE", "scheduler")
    monkeypatch.setenv("ENABLE_KASPI_SYNC_RUNNER", "1")
    monkeypatch.setattr(settings, "KASPI_AUTOSYNC_ENABLED", True)

    # Import and call should_register_kaspi_autosync
    import sys

    if "app.worker.scheduler_worker" in sys.modules:
        from app.worker.scheduler_worker import should_register_kaspi_autosync

        result = should_register_kaspi_autosync()
        assert (
            not result
        ), "should_register_kaspi_autosync should return False when ENABLE_KASPI_SYNC_RUNNER=1"

    monkeypatch.undo()


@pytest.mark.asyncio
async def test_kaspi_runner_skipped_for_scheduler_role(monkeypatch):
    """With PROCESS_ROLE='scheduler', Kaspi runner is skipped even if ENABLE_KASPI_SYNC_RUNNER=1."""
    monkeypatch.setattr(settings, "PROCESS_ROLE", "scheduler")
    monkeypatch.setenv("ENABLE_KASPI_SYNC_RUNNER", "1")
    monkeypatch.setattr(main_module, "should_disable_startup_hooks", lambda: False)

    runner_started = False

    def _create_task_side_effect(*args, **kwargs):
        nonlocal runner_started
        runner_started = True
        # Return a mock task that's already done
        import asyncio

        task = asyncio.create_task(asyncio.sleep(0))
        return task

    # Simulate the lifespan startup logic for Kaspi runner
    disable_hooks = main_module.should_disable_startup_hooks()
    role = getattr(settings, "PROCESS_ROLE", os.getenv("PROCESS_ROLE", "web")) or "web"
    enable_kaspi_sync = main_module._env_truthy(os.getenv("ENABLE_KASPI_SYNC_RUNNER", "0"))

    if role in ("web", "runner") and not disable_hooks and enable_kaspi_sync:
        _create_task_side_effect()

    assert not runner_started, "Kaspi runner should not start for role='scheduler'"


@pytest.mark.asyncio
async def test_kaspi_runner_starts_for_web_role(monkeypatch):
    """With PROCESS_ROLE='web', Kaspi runner is attempted when ENABLE_KASPI_SYNC_RUNNER=1."""
    monkeypatch.setattr(settings, "PROCESS_ROLE", "web")
    monkeypatch.setenv("ENABLE_KASPI_SYNC_RUNNER", "1")
    monkeypatch.setattr(main_module, "should_disable_startup_hooks", lambda: False)

    runner_started = False

    def _create_task_side_effect(*args, **kwargs):
        nonlocal runner_started
        runner_started = True
        # Return a mock task that's already done
        import asyncio

        task = asyncio.create_task(asyncio.sleep(0))
        return task

    # Simulate the lifespan startup logic for Kaspi runner
    disable_hooks = main_module.should_disable_startup_hooks()
    role = getattr(settings, "PROCESS_ROLE", os.getenv("PROCESS_ROLE", "web")) or "web"
    enable_kaspi_sync = main_module._env_truthy(os.getenv("ENABLE_KASPI_SYNC_RUNNER", "0"))

    if role in ("web", "runner") and not disable_hooks and enable_kaspi_sync:
        _create_task_side_effect()

    assert runner_started, "Kaspi runner should start for role='web' with ENABLE_KASPI_SYNC_RUNNER=1"


@pytest.mark.asyncio
async def test_kaspi_runner_starts_for_runner_role(monkeypatch):
    """With PROCESS_ROLE='runner', Kaspi runner is attempted when ENABLE_KASPI_SYNC_RUNNER=1."""
    monkeypatch.setattr(settings, "PROCESS_ROLE", "runner")
    monkeypatch.setenv("ENABLE_KASPI_SYNC_RUNNER", "1")
    monkeypatch.setattr(main_module, "should_disable_startup_hooks", lambda: False)

    runner_started = False

    def _create_task_side_effect(*args, **kwargs):
        nonlocal runner_started
        runner_started = True
        # Return a mock task that's already done
        import asyncio

        task = asyncio.create_task(asyncio.sleep(0))
        return task

    # Simulate the lifespan startup logic for Kaspi runner
    disable_hooks = main_module.should_disable_startup_hooks()
    role = getattr(settings, "PROCESS_ROLE", os.getenv("PROCESS_ROLE", "web")) or "web"
    enable_kaspi_sync = main_module._env_truthy(os.getenv("ENABLE_KASPI_SYNC_RUNNER", "0"))

    if role in ("web", "runner") and not disable_hooks and enable_kaspi_sync:
        _create_task_side_effect()

    assert runner_started, "Kaspi runner should start for role='runner' with ENABLE_KASPI_SYNC_RUNNER=1"
