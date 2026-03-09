from __future__ import annotations

import asyncio

import httpx


def _safe_httpx_request(exc: Exception) -> httpx.Request | None:
    try:
        return getattr(exc, "request", None)
    except RuntimeError:
        return None


def _safe_httpx_response(exc: Exception) -> httpx.Response | None:
    try:
        return getattr(exc, "response", None)
    except RuntimeError:
        return None


def _safe_httpx_url_path(exc: Exception) -> str | None:
    try:
        req = getattr(exc, "request", None)
        if not req:
            return None
        url = getattr(req, "url", None)
        if not url:
            return None
        return getattr(url, "path", None)
    except Exception:
        return None


def _extract_httpx_root_cause(exc: Exception) -> tuple[str | None, str | None]:
    cause = getattr(exc, "__cause__", None) or getattr(exc, "__context__", None)
    if not cause:
        return None, None
    return type(cause).__name__, str(cause)


def _classify_httpx_error(exc: Exception, root_type: str | None, root_message: str | None) -> str:
    if isinstance(exc, httpx.TimeoutException):
        return "timeout"
    if isinstance(exc, httpx.ConnectTimeout):
        return "connect_timeout"
    if isinstance(exc, httpx.ReadTimeout):
        return "read_timeout"
    if isinstance(exc, httpx.WriteTimeout):
        return "write_timeout"
    if isinstance(exc, httpx.ConnectError):
        return "connect_error"
    cause_type = (root_type or "").lower()
    cause_msg = (root_message or "").lower()
    if "ssl" in cause_type or "tls" in cause_type or "ssl" in cause_msg or "tls" in cause_msg:
        return "tls_error"
    if "dns" in cause_type or "gaierror" in cause_type or "name or service" in cause_msg:
        return "dns_error"
    return "request_error"


class _RetryingAsyncClient:
    """
    Обёртка над httpx.AsyncClient с экспоненциальными повторами на сетевые и 5xx ошибки.
    Поддерживает async context manager (`async with`) для корректного закрытия соединений.
    """

    def __init__(
        self,
        *,
        timeout: float | httpx.Timeout = 30.0,
        retries: int = 2,
        backoff_base: float = 0.5,
    ):
        if isinstance(timeout, int | float):
            timeout = httpx.Timeout(timeout)
        self._client = httpx.AsyncClient(timeout=timeout)
        self._retries = max(0, retries)
        self._base = backoff_base

    async def __aenter__(self) -> _RetryingAsyncClient:
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.aclose()

    async def request(self, method: str, url: str, **kwargs) -> httpx.Response:
        last_exc: Exception | None = None
        for attempt in range(self._retries + 1):
            try:
                resp = await self._client.request(method, url, **kwargs)
                if 500 <= resp.status_code < 600:
                    raise httpx.HTTPStatusError("Server error", request=resp.request, response=resp)
                return resp
            except (httpx.TimeoutException, httpx.NetworkError, httpx.HTTPStatusError) as e:
                last_exc = e
                if attempt >= self._retries:
                    break
                await asyncio.sleep(self._base * (2**attempt))
        assert last_exc is not None
        raise last_exc

    async def get(self, url: str, **kwargs) -> httpx.Response:
        return await self.request("GET", url, **kwargs)

    async def patch(self, url: str, **kwargs) -> httpx.Response:
        return await self.request("PATCH", url, **kwargs)

    async def post(self, url: str, **kwargs) -> httpx.Response:
        return await self.request("POST", url, **kwargs)

    async def aclose(self) -> None:
        await self._client.aclose()
