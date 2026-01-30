from __future__ import annotations

import httpx
from fastapi import status

from app.core.exceptions import http_error
from app.core.logging import get_logger

logger = get_logger(__name__)

DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36",
    "Accept": "application/json,text/plain,*/*",
    "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
}


class KaspiNotAuthenticated(RuntimeError):
    """Kaspi token is invalid or not authenticated."""


class KaspiGoodsClient:
    def __init__(self, *, token: str, base_url: str = "https://kaspi.kz"):
        if not token:
            raise ValueError("kaspi_token_required")
        self._token = token
        self._base_url = base_url.rstrip("/")
        self._default_timeout = 30.0
        self._fast_timeout = 8.0

    def _headers(self) -> dict[str, str]:
        return self._build_headers(self._token)

    @staticmethod
    def _build_headers(token: str, extra: dict[str, str] | None = None) -> dict[str, str]:
        headers = {**DEFAULT_HEADERS, "X-Auth-Token": token}
        if extra:
            headers.update(extra)
        return headers

    def _url(self, path: str) -> str:
        if not path.startswith("/"):
            path = "/" + path
        return f"{self._base_url}{path}"

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        json_body: object | None = None,
        content_type: str | None = None,
        timeout_sec: float | None = None,
    ) -> dict:
        extra_headers: dict[str, str] = {}
        if content_type:
            extra_headers["Content-Type"] = content_type
        headers = self._build_headers(self._token, extra=extra_headers)
        timeout_value = float(timeout_sec) if timeout_sec is not None else self._default_timeout
        try:
            async with httpx.AsyncClient(timeout=timeout_value) as client:
                resp = await client.request(method, self._url(path), headers=headers, params=params, json=json_body)
        except (httpx.TimeoutException, httpx.RequestError) as exc:
            logger.warning("Kaspi goods upstream unavailable: %s", exc)
            raise http_error(status.HTTP_502_BAD_GATEWAY, "kaspi_upstream_unavailable") from exc
        if resp.status_code == 401:
            raise KaspiNotAuthenticated("Kaspi token is not authenticated")
        resp.raise_for_status()
        return resp.json() if resp.content else {}

    async def get_schema(self) -> dict:
        return await self._request("GET", "/shop/api/products/import/schema", timeout_sec=self._fast_timeout)

    async def get_categories(self) -> dict:
        return await self._request(
            "GET", "/shop/api/products/classification/categories", timeout_sec=self._fast_timeout
        )

    async def get_attributes(self, *, category_code: str) -> dict:
        return await self._request(
            "GET",
            "/shop/api/products/classification/attributes",
            params={"c": category_code},
        )

    async def get_attribute_values(self, *, category_code: str, attribute_code: str) -> dict:
        return await self._request(
            "GET",
            "/shop/api/products/classification/attribute/values",
            params={"c": category_code, "a": attribute_code},
        )

    async def post_import(self, payload: list[dict], *, content_type: str | None = None) -> dict:
        return await self._request(
            "POST",
            "/shop/api/products/import",
            json_body=payload,
            content_type=content_type or "application/json",
        )

    async def get_import_status(self, *, import_code: str) -> dict:
        return await self._request(
            "GET",
            "/shop/api/products/import",
            params={"i": import_code},
        )

    async def get_import_result(self, *, import_code: str) -> dict:
        return await self._request(
            "GET",
            "/shop/api/products/import/result",
            params={"i": import_code},
        )
