from __future__ import annotations

import httpx

from app.core.logging import get_logger

logger = get_logger(__name__)


class KaspiNotAuthenticated(RuntimeError):
    """Kaspi token is invalid or not authenticated."""


class KaspiGoodsClient:
    def __init__(self, *, token: str, base_url: str = "https://kaspi.kz"):
        if not token:
            raise ValueError("kaspi_token_required")
        self._token = token
        self._base_url = base_url.rstrip("/")

    def _headers(self) -> dict[str, str]:
        return {
            "X-Auth-Token": self._token,
            "Accept": "application/json",
        }

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
    ) -> dict:
        headers = self._headers()
        if content_type:
            headers["Content-Type"] = content_type
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.request(method, self._url(path), headers=headers, params=params, json=json_body)
        if resp.status_code == 401:
            raise KaspiNotAuthenticated("Kaspi token is not authenticated")
        resp.raise_for_status()
        return resp.json() if resp.content else {}

    async def get_schema(self) -> dict:
        return await self._request("GET", "/shop/api/products/import/schema")

    async def get_categories(self) -> dict:
        return await self._request("GET", "/shop/api/products/classification/categories")

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
