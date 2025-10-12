# tests/_compat.py
"""
TestClient await-compat shim.

Цель:
- Позволить использовать синхронный fastapi.TestClient как привычно (client.get(...)),
  И ПРИ ЭТОМ — безопасно писать `await client.get(...)` в async-тестах.
- Без изменения остального кода тестов и без побочных эффектов.

Как работает:
- Оборачиваем методы TestClient (get/post/put/patch/delete/options/head/request),
  возвращая объект-обёртку _AwaitableResponse.
- _AwaitableResponse:
    * ведёт себя как обычный Response (проксирует атрибуты к исходному Response),
    * одновременно является awaitable: `await _AwaitableResponse` просто вернёт исходный Response.
- Патч идемпотентный: повторные вызовы не дублируют обёртки.

Важно:
- Мы НЕ переводим вызовы в реальный асинхронный режим — TestClient остаётся синхронным внутри.
  Это ровно то, что нужно для совместимости: sync-тесты продолжают работать как прежде,
  async-тесты могут `await`-ить вызов без падений (получив тот же готовый Response).
"""

from __future__ import annotations

import functools
from collections.abc import Callable
from typing import Any

try:
    from fastapi.testclient import TestClient
except Exception:  # pragma: no cover
    TestClient = None  # type: ignore[assignment]


class _AwaitableResponse:
    """Обёртка, которая и выглядит как Response, и может быть 'await'-нута."""

    __slots__ = ("_resp",)

    def __init__(self, resp: Any) -> None:
        self._resp = resp

    # Проксирование всех атрибутов/методов к исходному Response
    def __getattr__(self, name: str) -> Any:
        return getattr(self._resp, name)

    def __repr__(self) -> str:  # pragma: no cover
        return f"_AwaitableResponse({self._resp!r})"

    # Делает объект awaitable: await wrapper -> вернёт исходный Response
    def __await__(self):
        async def _return_resp():
            return self._resp

        return _return_resp().__await__()


def _wrap_method(method: Callable[..., Any]) -> Callable[..., _AwaitableResponse]:
    @functools.wraps(method)
    def _wrapped(*args, **kwargs) -> _AwaitableResponse:
        resp = method(*args, **kwargs)
        # Возвращаем обёртку: синхронный доступ работает сразу,
        # а `await obj` вернёт тот же resp.
        return _AwaitableResponse(resp)

    # Помечаем как уже обёрнутый, чтобы избежать двойной обёртки
    setattr(_wrapped, "__await_shim__", True)
    return _wrapped


def patch_testclient_async_await() -> None:
    """Патчит методы TestClient так, чтобы их можно было await-ить.

    Идемпотентно: повторный вызов безопасен.
    """
    if TestClient is None:  # pragma: no cover
        return

    methods = ("get", "post", "put", "patch", "delete", "options", "head", "request")

    for name in methods:
        if not hasattr(TestClient, name):
            continue
        orig = getattr(TestClient, name)
        # Уже патчено?
        if getattr(orig, "__await_shim__", False):
            continue
        wrapped = _wrap_method(orig)
        setattr(TestClient, name, wrapped)
