from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

import app.core.security as security


def _force_inmemory_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(security, "_HAS_SQLA", False, raising=False)
    monkeypatch.setattr(security, "_SYNC_ENGINE_AVAILABLE", False, raising=False)


def test_denylist_skips_redis_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REDIS_DISABLED", "1")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setattr(security, "_HAS_REDIS", True, raising=False)
    _force_inmemory_backend(monkeypatch)

    backend = security._build_denylist_backend()

    assert not isinstance(backend, security.RedisDenylist)


def test_denylist_skips_redis_when_url_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("REDIS_DISABLED", raising=False)
    monkeypatch.setenv("REDIS_URL", "disabled")
    monkeypatch.setattr(security, "_HAS_REDIS", True, raising=False)
    _force_inmemory_backend(monkeypatch)

    backend = security._build_denylist_backend()

    assert not isinstance(backend, security.RedisDenylist)


def test_denylist_falls_back_when_redis_ping_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeConnectionError(Exception):
        pass

    class FakeRedisClient:
        def ping(self) -> None:
            raise FakeConnectionError("redis down")

        def exists(self, key: str) -> int:
            return 0

        def set(self, key: str, value: str, ex: int | None = None) -> None:
            return None

        def scan(self, cursor: str = "0", match: str | None = None, count: int | None = None):
            return "0", []

    class FakeRedisModule(SimpleNamespace):
        ConnectionError = FakeConnectionError

        class Redis:  # type: ignore[override]
            @staticmethod
            def from_url(*_args, **_kwargs):
                return FakeRedisClient()

    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setattr(security, "redis", FakeRedisModule(), raising=False)
    monkeypatch.setattr(security, "_HAS_REDIS", True, raising=False)
    _force_inmemory_backend(monkeypatch)

    backend = security._build_denylist_backend()
    assert not isinstance(backend, security.RedisDenylist)

    security._DENYLIST = backend
    t0 = time.perf_counter()
    assert security.is_token_revoked("test-jti") is False
    assert (time.perf_counter() - t0) < 0.5
