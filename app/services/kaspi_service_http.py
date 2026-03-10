from __future__ import annotations

from datetime import UTC, datetime, timedelta
from time import perf_counter
from typing import Any, Optional

import anyio
import httpx

from app.core.config import settings
from app.core.logging import get_logger
from app.services.kaspi_service_transport import _classify_httpx_error, _extract_httpx_root_cause
from app.services.kaspi_service_utils import (
    _diag_enabled,
    _extract_kaspi_error_title,
    _first_present,
    _mask_token,
    _response_snippet,
    _utcnow,
)

logger = get_logger(__name__)


def _kaspi_bad_request_error(message: str, *, status_code: int | None = None) -> RuntimeError:
    from app.services.kaspi_service import KaspiBadRequestError

    return KaspiBadRequestError(message, status_code=status_code)


def _kaspi_products_upstream_error(code: str, *, status_code: int | None = None) -> RuntimeError:
    from app.services.kaspi_service import KaspiProductsUpstreamError

    return KaspiProductsUpstreamError(code, status_code=status_code)


class KaspiServiceHttpMixin:
    def _client(
        self,
        *,
        timeout: float | httpx.Timeout | None = None,
        retries: int | None = None,
        backoff_base: float = 0.5,
    ):
        from app.services.kaspi_service_transport import _RetryingAsyncClient

        effective_timeout = 30.0 if timeout is None else timeout
        effective_retries = 2 if retries is None else max(0, int(retries))
        return _RetryingAsyncClient(timeout=effective_timeout, retries=effective_retries, backoff_base=backoff_base)

    def _products_timeout(self) -> httpx.Timeout:
        total_timeout = max(60.0, float(getattr(settings, "KASPI_HTTP_TIMEOUT_SEC", 60) or 60))
        connect_timeout = max(20.0, float(getattr(settings, "KASPI_ORDERS_CONNECT_TIMEOUT_SEC", 20) or 20))
        return httpx.Timeout(total_timeout, connect=connect_timeout)

    def _products_client(self) -> httpx.AsyncClient:
        limits = httpx.Limits(max_connections=1, max_keepalive_connections=0)
        headers = {
            "Connection": "close",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36",
            "Accept": "application/json",
        }
        return httpx.AsyncClient(
            timeout=self._products_timeout(),
            limits=limits,
            headers=headers,
            trust_env=False,
            http2=False,
        )

    def _products_headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if self.api_key:
            headers["X-Auth-Token"] = self.api_key
        return headers

    def _orders_timeout(self, total_sec: float | None = None) -> httpx.Timeout:
        if total_sec is not None:
            total = max(1.0, float(total_sec))
            read = max(5.0, total - 5.0)
            connect = min(10.0, total)
            write = min(10.0, total)
            pool = min(10.0, total)
            return httpx.Timeout(connect=connect, read=read, write=write, pool=pool)

        connect = float(getattr(settings, "KASPI_ORDERS_CONNECT_TIMEOUT_SEC", 3) or 3)
        rw_pool = float(getattr(settings, "KASPI_ORDERS_TIMEOUT_SEC", 8) or 8)
        return httpx.Timeout(connect=connect, read=rw_pool, write=rw_pool, pool=rw_pool)

    def _url(self, path: str) -> str:
        if not path.startswith("/"):
            path = "/" + path
        return f"{self.base_url}{path}"

    def _orders_url(self) -> str:
        return settings.kaspi_orders_url()

    def _orders_headers(self) -> dict[str, str]:
        try:
            headers = settings.kaspi_jsonapi_headers(self.api_key)
        except ValueError:
            headers = {"Accept": "application/vnd.api+json"}
            if self.api_key:
                headers["X-Auth-Token"] = self.api_key
        headers.setdefault("Accept", "application/vnd.api+json")
        headers.setdefault("Content-Type", "application/vnd.api+json")
        headers.setdefault("User-Agent", f"{settings.PROJECT_NAME}/{settings.VERSION}")
        return headers

    def _orders_client(self, *, timeout: httpx.Timeout) -> httpx.AsyncClient:
        http2_enabled = bool(getattr(settings, "KASPI_HTTP2", False))
        if http2_enabled:
            return httpx.AsyncClient(timeout=timeout, trust_env=False, http2=True)
        transport = httpx.AsyncHTTPTransport(http2=False)
        return httpx.AsyncClient(timeout=timeout, trust_env=False, transport=transport)

    async def _orders_http_request(
        self,
        *,
        method: str,
        url: str,
        headers: dict[str, str],
        params: list[tuple[str, object]] | None,
        json: dict[str, Any] | None,
        timeout: httpx.Timeout,
    ) -> httpx.Response:
        transport_mode = str(getattr(settings, "KASPI_ORDERS_TRANSPORT", "async") or "async").lower()
        method_value = method.upper()
        wants_get = method_value == "GET"

        if transport_mode == "sync":
            read_timeout = getattr(timeout, "read", None)
            sync_timeout = httpx.Timeout(
                connect=getattr(timeout, "connect", None),
                read=max(float(read_timeout or 0.0), 10.0),
                write=getattr(timeout, "write", None),
                pool=getattr(timeout, "pool", None),
            )

            def _do_request() -> httpx.Response:
                transport = httpx.HTTPTransport(http2=False)
                with httpx.Client(timeout=sync_timeout, trust_env=False, transport=transport) as client:
                    if wants_get and callable(getattr(client, "get", None)):
                        return client.get(url, headers=headers, params=params)
                    return client.request(method_value, url, headers=headers, params=params, json=json)

            return await anyio.to_thread.run_sync(_do_request, abandon_on_cancel=True)

        async with self._orders_client(timeout=timeout) as client:
            if wants_get and callable(getattr(client, "get", None)):
                return await client.get(url, headers=headers, params=params)
            return await client.request(method_value, url, headers=headers, params=params, json=json)

    async def _orders_http_get(
        self,
        *,
        url: str,
        headers: dict[str, str],
        params: list[tuple[str, object]],
        timeout: httpx.Timeout,
    ) -> httpx.Response:
        return await self._orders_http_request(
            method="GET",
            url=url,
            headers=headers,
            params=params,
            json=None,
            timeout=timeout,
        )

    def _orders_params(
        self,
        *,
        date_from: datetime,
        date_to: datetime,
        state: str | None,
        page: int,
        page_size: int,
        merchant_uid: str | None,
        include_entries: bool = True,
    ) -> list[tuple[str, Any]]:
        page_number = max(1, int(page or 1))
        size_value = max(1, int(page_size or 100))

        params: list[tuple[str, Any]] = [
            ("page[number]", page_number),
            ("page[size]", size_value),
            ("filter[orders][creationDate][$ge]", int(settings.dt_to_ms_almaty(date_from))),
            ("filter[orders][creationDate][$le]", int(settings.dt_to_ms_almaty(date_to))),
        ]
        if merchant_uid:
            params.append(("filter[orders][merchantUid]", merchant_uid))
        if state:
            params.append(("filter[orders][state]", state))
        if include_entries:
            params.append(("include[orders]", "entries"))
        return params

    async def list_orders(
        self,
        *,
        token: str,
        merchant_uid: str,
        state: str | None,
        date_from_ms: int,
        date_to_ms: int,
        page: int,
        limit: int,
        include_entries: bool,
        request_id: str | None,
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        orders_url = "https://kaspi.kz/shop/api/v2/orders"
        params: list[tuple[str, Any]] = [
            ("page[number]", page),
            ("page[size]", limit),
            ("filter[orders][merchantUid]", merchant_uid),
            ("filter[orders][creationDate][$ge]", date_from_ms),
            ("filter[orders][creationDate][$le]", date_to_ms),
        ]
        if state:
            params.append(("filter[orders][state]", state))
        if include_entries:
            params.append(("include[orders]", "entries"))

        headers = {
            "X-Auth-Token": token,
            "Accept": "application/vnd.api+json",
        }

        timeout = self._orders_timeout(timeout_seconds)
        logger.info(
            "Kaspi list_orders timeout: timeout_sec=%s connect=%s read=%s write=%s pool=%s",
            timeout_seconds,
            getattr(timeout, "connect", None),
            getattr(timeout, "read", None),
            getattr(timeout, "write", None),
            getattr(timeout, "pool", None),
        )
        try:
            resp = await self._orders_http_get(
                url=orders_url,
                headers=headers,
                params=params,
                timeout=timeout,
            )
            if resp.status_code in {401, 403}:
                return {
                    "ok": False,
                    "code": "NOT_AUTHENTICATED",
                    "detail": "NOT_AUTHENTICATED",
                    "status_code": 401,
                    "request_id": request_id,
                }
            resp.raise_for_status()
            payload = resp.json() or {}
        except httpx.TimeoutException:
            return {
                "ok": False,
                "code": "upstream_timeout",
                "detail": "kaspi_timeout",
                "status_code": 504,
                "request_id": request_id,
            }
        except httpx.HTTPStatusError as exc:
            status_code = getattr(getattr(exc, "response", None), "status_code", None)
            code = "upstream_unavailable" if status_code and status_code >= 500 else "upstream_error"
            return {
                "ok": False,
                "code": code,
                "detail": "kaspi_upstream_error",
                "status_code": 502,
                "request_id": request_id,
            }
        except httpx.RequestError:
            return {
                "ok": False,
                "code": "upstream_unavailable",
                "detail": "kaspi_upstream_error",
                "status_code": 502,
                "request_id": request_id,
            }

        raw_items = payload.get("data") or payload.get("items") or payload.get("orders") or []
        data: list[dict[str, Any]] = []
        for item in raw_items:
            if not isinstance(item, dict):
                continue
            attrs = item.get("attributes") if isinstance(item.get("attributes"), dict) else {}
            order_id = str(item.get("id") or attrs.get("id") or "")
            creation_raw = attrs.get("creationDate") or attrs.get("createdAt")
            creation_date = None
            if creation_raw is not None:
                try:
                    creation_date = datetime.fromtimestamp(int(creation_raw) / 1000.0, tz=UTC)
                except Exception:
                    creation_date = None
            entries = attrs.get("entries") if include_entries else None
            data.append(
                {
                    "order_id": order_id,
                    "code": attrs.get("code") or attrs.get("orderCode"),
                    "creation_date": creation_date,
                    "state": attrs.get("state") or attrs.get("status"),
                    "total_price": attrs.get("totalPrice") or attrs.get("total_price"),
                    "customer": attrs.get("customer"),
                    "entries": entries,
                }
            )

        return {
            "ok": True,
            "data": data,
            "request_id": request_id,
        }

    async def get_orders(
        self,
        *,
        date_from: Optional[datetime] = None,
        date_to: Optional[datetime] = None,
        state: Optional[str] = None,
        status: Optional[str] = None,
        page: int = 1,
        page_size: int = 100,
        company_id: int | None = None,
        merchant_uid: str | None = None,
        request_id: str | None = None,
        timeout: float | httpx.Timeout | None = None,
        retries: int | None = None,
    ) -> dict[str, Any] | list[dict[str, Any]]:
        if not date_from:
            date_from = _utcnow() - timedelta(days=1)
        if not date_to:
            date_to = _utcnow()

        effective_state = state or status
        params = self._orders_params(
            date_from=date_from,
            date_to=date_to,
            state=effective_state,
            page=page,
            page_size=page_size,
            merchant_uid=merchant_uid,
        )
        orders_url = "https://kaspi.kz/shop/api/v2/orders"
        orders_base_url = "https://kaspi.kz/shop/api"

        started_at = perf_counter()
        timeout_obj = (
            timeout
            if isinstance(timeout, httpx.Timeout)
            else self._orders_timeout(float(timeout) if timeout is not None else None)
        )

        logger.info(
            "[CI_DIAG] kaspi_orders_http_entry",
            extra={
                "company_id": company_id,
                "request_id": request_id,
                "merchant_uid_present": bool(merchant_uid),
                "base_url": orders_base_url,
                "path": "/shop/api/v2/orders",
                "resolved_url": orders_url,
                "http2_enabled": bool(getattr(settings, "KASPI_HTTP2", False)),
                "orders_transport": str(getattr(settings, "KASPI_ORDERS_TRANSPORT", "async")),
                "params": params,
                "timeout_connect": getattr(timeout_obj, "connect", None),
                "timeout_read": getattr(timeout_obj, "read", None),
                "timeout_write": getattr(timeout_obj, "write", None),
                "timeout_pool": getattr(timeout_obj, "pool", None),
            },
        )
        logger.info(
            "kaspi_orders_http_start",
            extra={
                "company_id": company_id,
                "merchant_uid": merchant_uid,
                "request_id": request_id,
                "path": "/shop/api/v2/orders",
                "resolved_url": orders_url,
                "params": params,
            },
        )
        if _diag_enabled():
            logger.info(
                "[CI_DIAG] get_orders REAL HTTP CALL: page=%s page_size=%s state=%s monotonic=%s",
                page,
                page_size,
                effective_state,
                perf_counter(),
            )

        try:
            resp = await self._orders_http_get(
                url=orders_url,
                headers=self._orders_headers(),
                params=params,
                timeout=timeout_obj,
            )
        except (httpx.TimeoutException, httpx.NetworkError, httpx.HTTPStatusError) as e:
            from app.services.kaspi_service_transport import _safe_httpx_url_path

            duration_ms = int((perf_counter() - started_at) * 1000)
            status_code = getattr(getattr(e, "response", None), "status_code", None)
            timed_out = isinstance(e, httpx.TimeoutException)
            logger.warning(
                "[CI_DIAG] kaspi_orders_http_exc",
                extra={
                    "company_id": company_id,
                    "request_id": request_id,
                    "merchant_uid_present": bool(merchant_uid),
                    "path": _safe_httpx_url_path(e) or "/shop/api/v2/orders",
                    "resolved_url": orders_url,
                    "params": params,
                    "duration_ms": duration_ms,
                    "status_code": status_code,
                    "exc_type": type(e).__name__,
                    "classify": "read_timeout"
                    if isinstance(e, httpx.ReadTimeout)
                    else "connect_timeout"
                    if isinstance(e, httpx.ConnectTimeout)
                    else "timeout"
                    if isinstance(e, httpx.TimeoutException)
                    else "http_error"
                    if isinstance(e, httpx.HTTPStatusError)
                    else "network_error"
                    if isinstance(e, httpx.NetworkError)
                    else "error",
                },
            )
            logger.warning(
                "kaspi_orders_http_end",
                extra={
                    "company_id": company_id,
                    "merchant_uid": merchant_uid,
                    "request_id": request_id,
                    "path": "/shop/api/v2/orders",
                    "resolved_url": orders_url,
                    "params": params,
                    "duration_ms": duration_ms,
                    "status_code": status_code,
                    "timed_out": timed_out,
                },
            )
            logger.warning("Kaspi get_orders transient error: %s", e)
            raise
        except httpx.HTTPError as e:
            logger.error("Kaspi get_orders error: %s", e)
            raise RuntimeError(f"Failed to fetch orders from Kaspi: {e}") from e

        duration_ms = int((perf_counter() - started_at) * 1000)
        content_len = None
        try:
            content_len = len(resp.content) if resp.content is not None else None
        except Exception:
            content_len = None

        if resp.status_code == 400:
            payload = None
            try:
                payload = resp.json()
            except Exception:
                payload = None
            title = _extract_kaspi_error_title(payload) or "kaspi bad request"
            raise _kaspi_bad_request_error(title, status_code=resp.status_code)

        logger.info(
            "kaspi_orders_http_end",
            extra={
                "company_id": company_id,
                "merchant_uid": merchant_uid,
                "request_id": request_id,
                "path": "/shop/api/v2/orders",
                "resolved_url": orders_url,
                "params": params,
                "duration_ms": duration_ms,
                "status_code": resp.status_code,
                "timed_out": False,
            },
        )
        resp.raise_for_status()
        data = resp.json() or {}

        raw_items = data.get("data") or []
        included = data.get("included") or []
        meta = data.get("meta") or {}
        meta_total = meta.get("totalCount") if isinstance(meta, dict) else None
        total_count = None
        if meta_total is not None:
            try:
                total_count = int(meta_total)
            except (TypeError, ValueError):
                total_count = None

        entries_by_id: dict[str, dict[str, Any]] = {}
        if isinstance(included, list):
            for inc in included:
                if not isinstance(inc, dict):
                    continue
                inc_id = inc.get("id")
                attrs = inc.get("attributes") if isinstance(inc.get("attributes"), dict) else {}
                if inc_id:
                    entries_by_id[str(inc_id)] = attrs

        items: list[dict[str, Any]] = []
        for item in raw_items:
            if not isinstance(item, dict):
                continue
            attrs = item.get("attributes") if isinstance(item.get("attributes"), dict) else {}
            merged = {**attrs}
            if item.get("id") and "id" not in merged:
                merged["id"] = item.get("id")
            relationships = item.get("relationships") if isinstance(item.get("relationships"), dict) else {}
            rel_entries = relationships.get("entries") if isinstance(relationships.get("entries"), dict) else {}
            rel_data = rel_entries.get("data") if isinstance(rel_entries.get("data"), list) else []
            if rel_data and entries_by_id:
                merged["items"] = [
                    entries_by_id.get(str(rel.get("id")))
                    for rel in rel_data
                    if isinstance(rel, dict) and rel.get("id") in entries_by_id
                ]
            items.append(merged)

        has_next = _first_present(data, "hasNext", "has_next")
        next_page = _first_present(data, "nextPage", "next_page")
        total_pages = _first_present(meta, "pageCount", "pageCount", "total_pages", "totalPages")
        logger.info(
            "[CI_DIAG] kaspi_orders_http_exit",
            extra={
                "company_id": company_id,
                "request_id": request_id,
                "merchant_uid_present": bool(merchant_uid),
                "path": "/shop/api/v2/orders",
                "resolved_url": orders_url,
                "params": params,
                "status_code": resp.status_code,
                "duration_ms": duration_ms,
                "bytes_len": content_len,
                "page": page,
                "has_next": has_next,
                "total_pages": total_pages,
                "total_count": total_count,
            },
        )

        if total_count == 0:
            return {
                "ok": True,
                "status": "success",
                "count": 0,
                "items": [],
                "page": page,
                "total_pages": total_pages or 0,
                "has_next": False,
                "next_page": None,
                "links": data.get("links") or {},
                "meta": meta,
            }

        return {
            "ok": True,
            "status": "success",
            "count": total_count if total_count is not None else len(items),
            "items": items,
            "page": page,
            "total_pages": total_pages,
            "has_next": has_next,
            "next_page": next_page,
            "links": data.get("links") or {},
            "meta": meta,
            "included": included,
        }

    async def verify_token(self, *, store_name: str | None = None, token: str) -> bool:
        temp_service = self.__class__(api_key=token, base_url=self.base_url)

        try:
            logger.info("Kaspi verify_token: attempting minimal get_orders call store=%s", store_name or "N/A")
            await temp_service.get_orders(page_size=1)
            logger.info("Kaspi verify_token: success store=%s", store_name or "N/A")
            return True

        except httpx.HTTPStatusError as e:
            if e.response.status_code in (401, 403):
                logger.warning(
                    "Kaspi verify_token: auth failed store=%s status=%s", store_name or "N/A", e.response.status_code
                )
                raise
            logger.warning(
                "Kaspi verify_token: HTTP error store=%s status=%s", store_name or "N/A", e.response.status_code
            )
            raise

        except (httpx.TimeoutException, httpx.NetworkError) as e:
            logger.warning("Kaspi verify_token: network error store=%s error=%s", store_name or "N/A", type(e).__name__)
            raise

    def _extract_action_status(self, payload: Any) -> str | None:
        if not isinstance(payload, dict):
            return None
        data = payload.get("data")
        if isinstance(data, dict):
            attrs = data.get("attributes") if isinstance(data.get("attributes"), dict) else {}
            return attrs.get("state") or attrs.get("status") or data.get("state") or data.get("status") or None
        return payload.get("state") or payload.get("status") or None

    def _parse_retry_after(self, response: httpx.Response) -> int | None:
        value = response.headers.get("Retry-After") if response is not None else None
        if not value:
            return None
        try:
            return max(0, int(value))
        except (TypeError, ValueError):
            return None

    async def _order_action(
        self,
        *,
        action: str,
        external_id: str,
        merchant_uid: str | None,
        request_id: str | None,
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        timeout_obj = self._orders_timeout(timeout_seconds)
        params: list[tuple[str, object]] = []
        if merchant_uid:
            params.append(("merchantUid", merchant_uid))

        if action == "accept":
            url = settings.kaspi_order_accept_url(external_id)
            default_status = "CONFIRMED"
        else:
            url = settings.kaspi_order_cancel_url(external_id)
            default_status = "CANCELLED"

        try:
            resp = await self._orders_http_request(
                method="POST",
                url=url,
                headers=self._orders_headers(),
                params=params,
                json=None,
                timeout=timeout_obj,
            )
        except httpx.TimeoutException:
            return {
                "ok": False,
                "status": "error",
                "code": "upstream_unavailable",
                "message": "kaspi_timeout",
                "request_id": request_id,
                "upstream_status_code": None,
                "http_status": 502,
            }
        except httpx.RequestError:
            return {
                "ok": False,
                "status": "error",
                "code": "upstream_unavailable",
                "message": "kaspi_upstream_error",
                "request_id": request_id,
                "upstream_status_code": None,
                "http_status": 502,
            }

        status_code = resp.status_code
        if status_code in {401, 403}:
            return {
                "ok": False,
                "status": "error",
                "code": "upstream_auth_failed",
                "message": "kaspi_auth_failed",
                "request_id": request_id,
                "upstream_status_code": status_code,
                "http_status": 502,
            }
        if status_code == 404:
            return {
                "ok": False,
                "status": "error",
                "code": "upstream_not_found",
                "message": "kaspi_not_found",
                "request_id": request_id,
                "upstream_status_code": status_code,
                "http_status": 404,
            }
        if status_code in {409, 423}:
            return {
                "ok": False,
                "status": "locked",
                "code": "locked",
                "message": "kaspi_locked",
                "request_id": request_id,
                "upstream_status_code": status_code,
                "http_status": 423,
            }
        if status_code == 429:
            return {
                "ok": False,
                "status": "rate_limited",
                "code": "rate_limited",
                "message": "kaspi_rate_limited",
                "request_id": request_id,
                "upstream_status_code": status_code,
                "retry_after": self._parse_retry_after(resp),
                "http_status": 429,
            }
        if status_code >= 500:
            return {
                "ok": False,
                "status": "error",
                "code": "upstream_unavailable",
                "message": "kaspi_upstream_unavailable",
                "request_id": request_id,
                "upstream_status_code": status_code,
                "http_status": 502,
            }
        if status_code >= 400:
            return {
                "ok": False,
                "status": "error",
                "code": "upstream_error",
                "message": "kaspi_upstream_error",
                "request_id": request_id,
                "upstream_status_code": status_code,
                "http_status": 502,
            }

        payload: Any = None
        try:
            payload = resp.json()
        except Exception:
            payload = None

        kaspi_status = self._extract_action_status(payload) or default_status
        mapped_status = self._map_kaspi_status(kaspi_status)
        return {
            "ok": True,
            "status": "success",
            "code": "ok",
            "message": f"kaspi_order_{action}_success",
            "request_id": request_id,
            "upstream_status_code": status_code,
            "mapped_status": mapped_status,
            "kaspi_status": kaspi_status,
            "http_status": 200,
        }

    async def accept_order(
        self,
        *,
        external_id: str,
        merchant_uid: str | None,
        request_id: str | None,
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        return await self._order_action(
            action="accept",
            external_id=external_id,
            merchant_uid=merchant_uid,
            request_id=request_id,
            timeout_seconds=timeout_seconds,
        )

    async def cancel_order(
        self,
        *,
        external_id: str,
        merchant_uid: str | None,
        request_id: str | None,
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        return await self._order_action(
            action="cancel",
            external_id=external_id,
            merchant_uid=merchant_uid,
            request_id=request_id,
            timeout_seconds=timeout_seconds,
        )

    async def get_products(
        self,
        *,
        page: int = 1,
        page_size: int = 100,
        company_id: int | None = None,
        store_name: str | None = None,
        request_id: str | None = None,
    ) -> list[dict[str, Any]]:
        url = self._url("/products")
        params = {"page": page, "pageSize": page_size}
        masked_token = _mask_token(self.api_key)
        try:
            async with self._products_client() as client:
                resp = await client.get(
                    url,
                    headers=self._products_headers(),
                    params=params,
                )
        except httpx.TimeoutException as exc:
            root_type, root_message = _extract_httpx_root_cause(exc)
            error_kind = _classify_httpx_error(exc, root_type, root_message)
            logger.warning(
                "Kaspi get_products timeout",
                extra={
                    "company_id": company_id,
                    "store_name": store_name,
                    "request_id": request_id,
                    "url": url,
                    "params": params,
                    "token_masked": masked_token,
                    "exc_type": type(exc).__name__,
                    "exc_repr": repr(exc),
                    "error_kind": error_kind,
                },
            )
            raise _kaspi_products_upstream_error(error_kind) from exc
        except httpx.RequestError as exc:
            root_type, root_message = _extract_httpx_root_cause(exc)
            error_kind = _classify_httpx_error(exc, root_type, root_message)
            logger.warning(
                "Kaspi get_products request error",
                extra={
                    "company_id": company_id,
                    "store_name": store_name,
                    "request_id": request_id,
                    "url": url,
                    "params": params,
                    "token_masked": masked_token,
                    "exc_type": type(exc).__name__,
                    "exc_repr": repr(exc),
                    "error_kind": error_kind,
                },
            )
            raise _kaspi_products_upstream_error(error_kind) from exc

        if resp.status_code in {401, 403}:
            logger.warning(
                "Kaspi get_products unauthorized",
                extra={
                    "company_id": company_id,
                    "store_name": store_name,
                    "request_id": request_id,
                    "url": url,
                    "params": params,
                    "token_masked": masked_token,
                    "status_code": resp.status_code,
                    "response_snippet": _response_snippet(resp.text),
                },
            )
            raise _kaspi_products_upstream_error("NOT_AUTHENTICATED", status_code=resp.status_code)

        if resp.status_code in {301, 302}:
            logger.warning(
                "Kaspi get_products redirect",
                extra={
                    "company_id": company_id,
                    "store_name": store_name,
                    "request_id": request_id,
                    "url": url,
                    "params": params,
                    "token_masked": masked_token,
                    "status_code": resp.status_code,
                    "location": resp.headers.get("Location"),
                    "response_snippet": _response_snippet(resp.text),
                },
            )
            raise _kaspi_products_upstream_error(f"http_status_{resp.status_code}", status_code=resp.status_code)

        if not (200 <= resp.status_code < 300):
            logger.warning(
                "Kaspi get_products http error",
                extra={
                    "company_id": company_id,
                    "store_name": store_name,
                    "request_id": request_id,
                    "url": url,
                    "params": params,
                    "token_masked": masked_token,
                    "status_code": resp.status_code,
                    "response_snippet": _response_snippet(resp.text),
                },
            )
            raise _kaspi_products_upstream_error("http_status", status_code=resp.status_code)

        try:
            data = resp.json() or {}
        except Exception as exc:
            logger.warning(
                "Kaspi get_products invalid JSON",
                extra={
                    "company_id": company_id,
                    "store_name": store_name,
                    "request_id": request_id,
                    "url": url,
                    "params": params,
                    "token_masked": masked_token,
                    "status_code": resp.status_code,
                    "response_snippet": _response_snippet(resp.text),
                    "exc_type": type(exc).__name__,
                    "exc_repr": repr(exc),
                },
            )
            raise _kaspi_products_upstream_error("http_status", status_code=resp.status_code) from exc

        return data.get("products") or data.get("items") or []

    async def update_product_availability(self, product_id: str, availability: int) -> bool:
        async with self._client() as client:
            try:
                resp = await client.patch(
                    self._url(f"/products/{product_id}/availability"),
                    headers=self.headers,
                    json={"availability": int(max(0, availability))},
                )
                resp.raise_for_status()
                logger.info("Kaspi: обновлена доступность товара %s -> %s.", product_id, availability)
                return True
            except httpx.HTTPError as e:
                logger.error("Kaspi update_product_availability(%s) error: %s", product_id, e)
                return False

    async def upload_products_feed(self, xml_payload: str) -> bool:
        try:
            if not xml_payload or not isinstance(xml_payload, str):
                raise ValueError("Invalid payload: must be non-empty XML string")

            logger.info("Kaspi: feed upload stub called with %d bytes", len(xml_payload))
            return True
        except Exception as e:
            logger.error("Kaspi upload_products_feed error: %s", e)
            raise RuntimeError(f"Failed to upload products feed to Kaspi: {e}") from e
