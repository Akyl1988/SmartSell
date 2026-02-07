from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any


def _is_retriable(exc: Exception) -> bool:
    if isinstance(exc, asyncio.TimeoutError | TimeoutError | ConnectionError | OSError):
        return True
    try:  # optional httpx
        import httpx  # type: ignore

        if isinstance(exc, httpx.TimeoutException | httpx.NetworkError):
            return True
    except Exception:
        pass
    return False


class RetryPolicy:
    def __init__(
        self,
        *,
        timeout_seconds: float = 10.0,
        retries: int = 2,
        backoff_seconds: float = 0.5,
        max_backoff_seconds: float = 5.0,
        backoff_multiplier: float = 2.0,
    ) -> None:
        self.timeout_seconds = max(0.1, float(timeout_seconds))
        self.retries = max(0, int(retries))
        self.backoff_seconds = max(0.0, float(backoff_seconds))
        self.max_backoff_seconds = max(0.0, float(max_backoff_seconds))
        self.backoff_multiplier = max(1.0, float(backoff_multiplier))

    async def run(self, func: Callable[..., Awaitable[Any]], *args: Any, **kwargs: Any) -> Any:
        attempt = 0
        delay = self.backoff_seconds
        while True:
            try:
                return await asyncio.wait_for(func(*args, **kwargs), timeout=self.timeout_seconds)
            except Exception as exc:  # pragma: no cover - exercised via tests
                if attempt >= self.retries or not _is_retriable(exc):
                    raise
                await asyncio.sleep(min(self.max_backoff_seconds, delay))
                delay = delay * self.backoff_multiplier
                attempt += 1


__all__ = ["RetryPolicy"]
