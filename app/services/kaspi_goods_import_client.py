from __future__ import annotations

import httpx

from app.services.kaspi_goods_client import DEFAULT_HEADERS


class KaspiImportUpstreamUnavailable(RuntimeError):
    """Kaspi import upstream unavailable (timeouts/network/5xx)."""


class KaspiImportUpstreamError(RuntimeError):
    """Kaspi import upstream error (4xx or unexpected)."""


class KaspiImportNotAuthenticated(RuntimeError):
    """Kaspi token is invalid or not authenticated."""


class KaspiGoodsImportClient:
    def __init__(
        self,
        *,
        token: str,
        base_url: str = "https://kaspi.kz",
        timeout_seconds: float = 8.0,
        max_attempts: int = 2,
    ) -> None:
        if not token:
            raise ValueError("kaspi_token_required")
        self._token = token
        self._base_url = base_url.rstrip("/")
        self._timeout = httpx.Timeout(timeout_seconds, connect=5.0, read=timeout_seconds, write=timeout_seconds)
        self._max_attempts = max(1, min(int(max_attempts), 3))

    def _url(self, path: str) -> str:
        if not path.startswith("/"):
            path = "/" + path
        return f"{self._base_url}{path}"

    def _headers(self, content_type: str | None = None) -> dict[str, str]:
        headers = {**DEFAULT_HEADERS, "X-Auth-Token": self._token}
        if content_type:
            headers["Content-Type"] = content_type
        return headers

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        content: str | bytes | None = None,
        content_type: str | None = None,
    ) -> dict:
        last_exc: Exception | None = None
        for attempt in range(self._max_attempts):
            try:
                async with httpx.AsyncClient(timeout=self._timeout, follow_redirects=True) as client:
                    resp = await client.request(
                        method,
                        self._url(path),
                        headers=self._headers(content_type),
                        params=params,
                        content=content,
                    )
                if resp.status_code in {401, 403}:
                    raise KaspiImportNotAuthenticated("Kaspi token is not authenticated")
                if resp.status_code >= 500:
                    raise KaspiImportUpstreamUnavailable("kaspi_upstream_unavailable")
                resp.raise_for_status()
                return resp.json() if resp.content else {}
            except (httpx.TimeoutException, httpx.RequestError) as exc:
                last_exc = exc
                if attempt + 1 < self._max_attempts:
                    continue
                raise KaspiImportUpstreamUnavailable("kaspi_upstream_unavailable") from exc
            except httpx.HTTPStatusError as exc:
                last_exc = exc
                if exc.response is not None and exc.response.status_code >= 500:
                    if attempt + 1 < self._max_attempts:
                        continue
                    raise KaspiImportUpstreamUnavailable("kaspi_upstream_unavailable") from exc
                raise KaspiImportUpstreamError("kaspi_upstream_error") from exc
        if last_exc:
            raise KaspiImportUpstreamUnavailable("kaspi_upstream_unavailable") from last_exc
        raise KaspiImportUpstreamUnavailable("kaspi_upstream_unavailable")

    async def submit_import(self, payload_json: str) -> dict:
        return await self._request(
            "POST",
            "/shop/api/products/import",
            content=payload_json,
            content_type="text/plain",
        )

    async def get_status(self, import_code: str) -> dict:
        return await self._request(
            "GET",
            "/shop/api/products/import",
            params={"i": import_code},
        )

    async def get_result(self, import_code: str) -> dict:
        return await self._request(
            "GET",
            "/shop/api/products/import/result",
            params={"i": import_code},
        )

    async def get_schema(self) -> dict:
        return await self._request("GET", "/shop/api/products/import/schema")
