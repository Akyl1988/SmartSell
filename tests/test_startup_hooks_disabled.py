import pytest

from app.core import provider_registry as pr
from app.core.config import settings, should_disable_startup_hooks
from app.main import create_app


@pytest.mark.asyncio
async def test_startup_side_effects_are_disabled_in_tests(monkeypatch):
    # Force test mode via env and settings so should_disable_startup_hooks() is true
    monkeypatch.setenv("TESTING", "1")
    monkeypatch.setenv("ENVIRONMENT", "testing")
    monkeypatch.setattr(settings, "TESTING", True, raising=False)
    monkeypatch.setattr(settings, "ENVIRONMENT", "testing", raising=False)

    def _fail(msg: str):
        def _raiser(*_args, **_kwargs):
            raise AssertionError(msg)

        return _raiser

    # Patch call-sites that schedule background work during startup
    monkeypatch.setattr(pr.asyncio, "create_task", _fail("provider listener started"))
    monkeypatch.setattr(
        __import__("app.main", fromlist=["asyncio"]).asyncio,
        "create_task",
        _fail("startup created background task"),
    )

    # Prevent DB access inside ProviderRegistry while exercising its guard
    async def _fake_load_active(db, domain):
        return None

    monkeypatch.setattr(pr.ProviderRegistry, "_load_active", staticmethod(_fake_load_active))
    pr.ProviderRegistry.invalidate()

    app = create_app()

    assert should_disable_startup_hooks() is True

    async with app.router.lifespan_context(app):
        await pr.ProviderRegistry.get_active_provider(None, "payments")

    # Explicit assertions that guards held
    assert pr.ProviderRegistry._listener_task is None
