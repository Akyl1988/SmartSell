from __future__ import annotations

"""
Kaspi.kz integration: product feed generation, orders sync, and availability sync.

- Асинхронный httpx-клиент с таймаутами и повторными попытками (экспоненциальный backoff).
- Безопасные мапперы статусов Kaspi -> внутренние статусы.
- Генерация XML-фида по активным товарам компании.
- Синхронизация заказов (загрузка, детализация, update статусов).
- Обновление доступности товаров на стороне Kaspi.
- Готово к дальнейшему расширению (ценовой фид/репрайсинг).

Зависимости: app.core.config.settings, app.core.logging.get_logger, app.models.{Order, OrderItem, Product}
"""

import asyncio
import hashlib
import os
import random
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager, nullcontext
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from time import perf_counter
from typing import Any, Optional

import httpx
from sqlalchemy import and_, literal_column, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.core.db import _get_async_engine
from app.core.errors import safe_error_message
from app.core.logging import get_logger
from app.integrations.kaspi_adapter import KaspiAdapterError
from app.models import Order, OrderItem, Product
from app.models.kaspi_order_sync_state import KaspiOrderSyncState
from app.models.order import OrderSource, OrderStatus, OrderStatusHistory

logger = get_logger(__name__)


class KaspiSyncAlreadyRunning(RuntimeError):
    pass


# ---------------------- small utils ---------------------- #


def _diag_enabled() -> bool:
    """Check if CI diagnostic logging is enabled."""
    return os.environ.get("CI_DIAG", "").strip() == "1"


def _utcnow() -> datetime:
    # Возвращаем naive UTC (совместимо с моделями)
    return datetime.utcnow()


def _as_str(v: Any) -> str:
    return "" if v is None else str(v)


def _first_present(data: Mapping[str, Any], *keys: str) -> Any | None:
    for key in keys:
        if key in data:
            return data[key]
    return None


def _xml_escape(s: str) -> str:
    return (
        s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;").replace("'", "&apos;")
    )


def _cdata(s: Optional[str]) -> str:
    if not s:
        return "<![CDATA[]]>"
    safe = s.replace("]]>", "]]&gt;")
    return f"<![CDATA[{safe}]]>"


# ---------------------- resilient HTTP client ---------------------- #


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
                # Повторяем на 5xx
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


# ---------------------- main service ---------------------- #


class KaspiService:
    """
    Базовые операции интеграции с Kaspi.kz:
      - загрузка заказов/деталей
      - обновление статуса заказа
      - загрузка товаров
      - генерация XML-фида
      - синхронизация заказов в локальную БД
      - (опционально) массовый апдейт доступности
    """

    def __init__(self, api_key: Optional[str] = None, base_url: Optional[str] = None):
        # Читаем из settings, но допускаем явную прокидку при создании сервиса
        self.api_key = api_key or getattr(settings, "KASPI_API_TOKEN", "") or ""
        self.base_url = (base_url or getattr(settings, "KASPI_API_URL", "") or "").rstrip("/")
        self.headers = {
            "Authorization": f"Bearer {self.api_key}" if self.api_key else "",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

        # Hard timeout guard for a single sync run (seconds).
        if not hasattr(self, "_sync_timeout_seconds"):
            self._sync_timeout_seconds = getattr(settings, "KASPI_SYNC_TIMEOUT_SECONDS", 30)

        if not self.base_url:
            logger.warning("KaspiService: BASE URL не задан (settings.KASPI_API_URL).")
        if not self.api_key:
            logger.warning("KaspiService: API ключ не задан (settings.KASPI_API_TOKEN).")

    # ---------------------- helpers ---------------------- #

    def _client(self, *, timeout: float | httpx.Timeout | None = None) -> _RetryingAsyncClient:
        # Единые сетевые настройки
        effective_timeout = 30.0 if timeout is None else timeout
        return _RetryingAsyncClient(timeout=effective_timeout, retries=2, backoff_base=0.5)

    def _orders_timeout(self) -> httpx.Timeout:
        connect = float(getattr(settings, "KASPI_ORDERS_CONNECT_TIMEOUT_SEC", 3) or 3)
        rw_pool = float(getattr(settings, "KASPI_ORDERS_TIMEOUT_SEC", 8) or 8)
        return httpx.Timeout(connect=connect, read=rw_pool, write=rw_pool, pool=rw_pool)

    def _url(self, path: str) -> str:
        if not path.startswith("/"):
            path = "/" + path
        return f"{self.base_url}{path}"

    # ---------------------- Orders API ---------------------- #

    async def get_orders(
        self,
        *,
        date_from: Optional[datetime] = None,
        date_to: Optional[datetime] = None,
        status: Optional[str] = None,
        page: int = 1,
        page_size: int = 100,
    ) -> dict[str, Any] | list[dict[str, Any]]:
        """
        Получение заказов из Kaspi.
        Если даты не заданы — последние 24 часа.
        Возвращает словарь (items + пагинация) или голый список для обратной совместимости.
        """
        if not date_from:
            date_from = _utcnow() - timedelta(days=1)
        if not date_to:
            date_to = _utcnow()

        params = {
            "page": page,
            "pageSize": page_size,
            "dateFrom": date_from.isoformat(),
            "dateTo": date_to.isoformat(),
        }
        if status:
            params["status"] = status

        async with self._client(timeout=self._orders_timeout()) as client:
            try:
                if _diag_enabled():
                    logger.info(
                        "[CI_DIAG] get_orders REAL HTTP CALL: page=%s page_size=%s status=%s monotonic=%s",
                        page,
                        page_size,
                        status,
                        perf_counter(),
                    )
                resp = await client.get(self._url("/orders"), headers=self.headers, params=params)
                resp.raise_for_status()
                data = resp.json() or {}
                items = data.get("orders") or data.get("items") or data.get("data")
                if items is None:
                    return []
                has_next = _first_present(data, "hasNext", "has_next")
                next_page = _first_present(data, "nextPage", "next_page")
                total_pages = _first_present(data, "totalPages", "pageCount", "total_pages")
                return {
                    "items": items,
                    "page": data.get("page") or data.get("pageNumber") or page,
                    "total_pages": total_pages,
                    "has_next": has_next,
                    "next_page": next_page,
                    "links": data.get("links") or {},
                }
            except (httpx.TimeoutException, httpx.NetworkError, httpx.HTTPStatusError) as e:
                logger.warning("Kaspi get_orders transient error: %s", e)
                raise
            except httpx.HTTPError as e:
                logger.error("Kaspi get_orders error: %s", e)
                raise RuntimeError(f"Failed to fetch orders from Kaspi: {e}") from e

    async def verify_token(self, *, store_name: str | None = None, token: str) -> bool:
        """
        Verify Kaspi API token validity by making a minimal API call.

        Uses get_orders with page_size=1 to check token authentication.
        Returns True if token is valid (HTTP 200), False or raises on auth/network errors.

        Raises:
            httpx.HTTPStatusError: on 401/403 (invalid token)
            httpx.HTTPError: on network/timeout errors
        """
        # Create temporary service with the provided token
        temp_service = KaspiService(api_key=token, base_url=self.base_url)

        try:
            # Make minimal request to verify token (last 24h, page_size=1)
            logger.info("Kaspi verify_token: attempting minimal get_orders call store=%s", store_name or "N/A")
            await temp_service.get_orders(page_size=1)

            # If we got here without exception, token is valid
            logger.info("Kaspi verify_token: success store=%s", store_name or "N/A")
            return True

        except httpx.HTTPStatusError as e:
            # Auth errors (401/403) mean invalid token
            if e.response.status_code in (401, 403):
                logger.warning(
                    "Kaspi verify_token: auth failed store=%s status=%s", store_name or "N/A", e.response.status_code
                )
                raise
            # Other HTTP errors are upstream problems
            logger.warning(
                "Kaspi verify_token: HTTP error store=%s status=%s", store_name or "N/A", e.response.status_code
            )
            raise

        except (httpx.TimeoutException, httpx.NetworkError) as e:
            # Network/timeout errors
            logger.warning("Kaspi verify_token: network error store=%s error=%s", store_name or "N/A", type(e).__name__)
            raise

    # ---------------------- Products API ---------------------- #

    async def get_products(self, *, page: int = 1, page_size: int = 100) -> list[dict[str, Any]]:
        async with self._client() as client:
            try:
                resp = await client.get(
                    self._url("/products"),
                    headers=self.headers,
                    params={"page": page, "pageSize": page_size},
                )
                resp.raise_for_status()
                data = resp.json() or {}
                return data.get("products") or data.get("items") or []
            except httpx.HTTPError as e:
                logger.error("Kaspi get_products error: %s", e)
                raise RuntimeError(f"Failed to fetch products from Kaspi: {e}") from e

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
        """
        Upload a products feed (XML) to Kaspi.
        Stub implementation for MVP: accepts XML and logs it.
        In production, would POST to Kaspi feed upload endpoint.
        """
        try:
            # Stub: for now just validate XML is valid and log
            if not xml_payload or not isinstance(xml_payload, str):
                raise ValueError("Invalid payload: must be non-empty XML string")

            # In production, would do:
            # async with self._client() as client:
            #     resp = await client.post(
            #         self._url("/feeds/upload"),
            #         headers={**self.headers, "Content-Type": "application/xml"},
            #         content=xml_payload.encode("utf-8"),
            #     )
            #     resp.raise_for_status()

            logger.info("Kaspi: feed upload stub called with %d bytes", len(xml_payload))
            return True
        except Exception as e:
            logger.error("Kaspi upload_products_feed error: %s", e)
            raise RuntimeError(f"Failed to upload products feed to Kaspi: {e}") from e

    # ---------------------- Orders sync (DB) ---------------------- #

    async def sync_orders(
        self,
        *,
        db: AsyncSession,
        company_id: int,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
        statuses: list[str] | None = None,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        """Инкрементальная и идемпотентная синхронизация заказов Kaspi."""
        attempt_at = _utcnow()
        effective_to = date_to or attempt_at
        overlap = timedelta(minutes=2)
        fetched = 0
        inserted = 0
        updated = 0
        started_at = perf_counter()
        timeout_seconds = float(self._sync_timeout_seconds or 30)

        logger.info("Kaspi orders sync start: company_id=%s request_id=%s", company_id, request_id)
        if _diag_enabled():
            logger.info(
                "[CI_DIAG] sync_orders ENTRY: company_id=%s request_id=%s timeout=%s monotonic=%s",
                company_id,
                request_id,
                timeout_seconds,
                perf_counter(),
            )

        try:
            async with asyncio.timeout(timeout_seconds):
                tx_ctx = nullcontext() if db.in_transaction() else db.begin()
                async with tx_ctx:
                    await db.execute(text("SET LOCAL lock_timeout = '2s'"))
                    await db.execute(text("SET LOCAL statement_timeout = '10s'"))

                    # Hold advisory lock for the entire sync to prevent concurrent watermark reads/updates.
                    async with self._company_lock(db, company_id):
                        state = await self._load_or_create_state(db, company_id)

                        state.last_attempt_at = attempt_at
                        state.last_result = None
                        state.last_duration_ms = None
                        state.last_fetched = None
                        state.last_inserted = None
                        state.last_updated = None

                        prev_last_synced = state.last_synced_at
                        prev_last_ext = state.last_external_order_id
                        is_new_state = prev_last_synced is None and prev_last_ext is None

                        base_from = date_from or prev_last_synced or (effective_to - timedelta(days=1))
                        effective_from = base_from - overlap

                        watermark = prev_last_synced or base_from
                        last_ext = prev_last_ext
                        made_progress = False

                        for status in statuses or [None]:
                            async for batch in self._iter_orders_pages(
                                date_from=effective_from,
                                date_to=effective_to,
                                status=status,
                                page_size=100,
                                company_id=company_id,
                            ):
                                fetched += len(batch)
                                for payload in batch:
                                    ext_id = _as_str(payload.get("id")).strip()
                                    if not ext_id:
                                        continue

                                    mapped_status = self._map_kaspi_status(_as_str(payload.get("status")))
                                    order_number = self._order_number_from_payload(company_id, ext_id, payload)
                                    customer = payload.get("customer") or {}
                                    currency = _as_str(payload.get("currency")) or "KZT"
                                    total_amount = self._decimal_or_zero(
                                        payload.get("totalPrice")
                                        or payload.get("total_amount")
                                        or payload.get("total")
                                        or 0
                                    )
                                    status_changed_at = self._extract_order_timestamp(payload)
                                    updated_ts = status_changed_at or effective_to
                                    effective_updated = updated_ts or attempt_at

                                    stmt = (
                                        insert(Order)
                                        .values(
                                            company_id=company_id,
                                            order_number=order_number,
                                            external_id=ext_id,
                                            source=OrderSource.KASPI,
                                            status=mapped_status,
                                            customer_phone=customer.get("phone") or None,
                                            customer_name=customer.get("name") or None,
                                            customer_address=payload.get("deliveryAddress")
                                            or payload.get("delivery_address")
                                            or None,
                                            delivery_method=payload.get("deliveryMode")
                                            or payload.get("delivery_mode")
                                            or None,
                                            total_amount=total_amount,
                                            currency=currency,
                                            updated_at=effective_updated,
                                        )
                                        .on_conflict_do_update(
                                            index_elements=[Order.company_id, Order.external_id],
                                            set_={
                                                "status": mapped_status,
                                                "source": OrderSource.KASPI,
                                                "order_number": order_number,
                                                "customer_phone": customer.get("phone") or None,
                                                "customer_name": customer.get("name") or None,
                                                "customer_address": payload.get("deliveryAddress")
                                                or payload.get("delivery_address")
                                                or None,
                                                "delivery_method": payload.get("deliveryMode")
                                                or payload.get("delivery_mode")
                                                or None,
                                                "total_amount": total_amount,
                                                "currency": currency,
                                                "updated_at": effective_updated,
                                            },
                                        )
                                        .returning(Order.id, literal_column("xmax = 0").label("inserted"))
                                    )

                                    async with db.begin_nested():
                                        res = await db.execute(stmt)

                                    row = res.one()
                                    inserted_flag: bool = bool(row.inserted)
                                    if inserted_flag:
                                        inserted += 1
                                    else:
                                        updated += 1

                                    order_pk = row.id

                                    if updated_ts > watermark or (
                                        updated_ts == watermark and ext_id and ext_id > (last_ext or "")
                                    ):
                                        watermark = updated_ts
                                        last_ext = ext_id
                                    made_progress = True

                                    items_updated = await self._upsert_order_items(
                                        db, order_id=order_pk, company_id=company_id, payload=payload
                                    )

                                    if items_updated:
                                        await self._recalculate_order_totals(db, order_id=order_pk)

                                    await self._upsert_status_history(
                                        db,
                                        order_id=order_pk,
                                        new_status=mapped_status,
                                        changed_at=status_changed_at,
                                    )

                                # page handling happens inside _iter_orders_pages

                            if made_progress or is_new_state:
                                final_wm = watermark if prev_last_synced is None else max(prev_last_synced, watermark)
                                state.last_synced_at = final_wm
                                state.last_external_order_id = last_ext
                            else:
                                final_wm = prev_last_synced or watermark
                                state.last_synced_at = prev_last_synced
                                state.last_external_order_id = prev_last_ext

                            finished_at = _utcnow()
                            duration_ms = int((perf_counter() - started_at) * 1000)
                            state.updated_at = finished_at
                            state.last_error_at = None
                            state.last_error_code = None
                            state.last_error_message = None
                            state.last_result = "success"
                            state.last_duration_ms = duration_ms
                            state.last_attempt_at = attempt_at
                            state.last_fetched = fetched
                            state.last_inserted = inserted
                            state.last_updated = updated
                if db.in_transaction():
                    await db.commit()
        except KaspiSyncAlreadyRunning:
            duration_ms = int((perf_counter() - started_at) * 1000)
            await self.record_sync_locked(
                db,
                company_id=company_id,
                attempt_at=attempt_at,
                duration_ms=duration_ms,
            )
            logger.warning(
                "Kaspi orders sync locked: company_id=%s request_id=%s duration_ms=%s",
                company_id,
                request_id,
                duration_ms,
            )
            return {
                "ok": False,
                "status": "locked",
                "code": "locked",
                "message": "kaspi orders sync locked",
                "company_id": company_id,
                "duration_ms": duration_ms,
            }
        except httpx.TimeoutException as exc:
            duration_ms = int((perf_counter() - started_at) * 1000)
            req = None
            try:
                req = exc.request
            except Exception:
                req = None
            url = getattr(req, "url", None)
            error_message = f"{exc.__class__.__name__} {url}" if url is not None else f"{exc.__class__.__name__}"

            await self.record_sync_error(
                db,
                company_id=company_id,
                code="kaspi_timeout",
                message=error_message,
                occurred_at=_utcnow(),
                attempt_at=attempt_at,
                duration_ms=duration_ms,
                fetched=fetched,
                inserted=inserted,
                updated=updated,
            )
            logger.error(
                "Kaspi orders sync timeout: company_id=%s request_id=%s duration_ms=%s",
                company_id,
                request_id,
                duration_ms,
            )
            return {
                "ok": False,
                "status": "timeout",
                "code": "timeout",
                "message": "kaspi orders sync timeout",
                "company_id": company_id,
                "duration_ms": duration_ms,
            }
        except httpx.HTTPStatusError as exc:
            duration_ms = int((perf_counter() - started_at) * 1000)
            status_code = getattr(getattr(exc, "response", None), "status_code", None)
            retry_after = self.get_retry_after_seconds(exc)

            if status_code == 429:
                await self.record_sync_error(
                    db,
                    company_id=company_id,
                    code="rate_limited",
                    message="kaspi rate limited",
                    occurred_at=_utcnow(),
                    attempt_at=attempt_at,
                    duration_ms=duration_ms,
                    fetched=fetched,
                    inserted=inserted,
                    updated=updated,
                )
                return {
                    "ok": False,
                    "status": "rate_limited",
                    "code": "rate_limited",
                    "message": "kaspi rate limited",
                    "company_id": company_id,
                    "duration_ms": duration_ms,
                    "retry_after": retry_after,
                }

            error_code = "upstream_unavailable" if status_code and status_code >= 500 else "internal_error"
            await self.record_sync_error(
                db,
                company_id=company_id,
                code=error_code,
                message=safe_error_message(exc),
                occurred_at=_utcnow(),
                attempt_at=attempt_at,
                duration_ms=duration_ms,
                fetched=fetched,
                inserted=inserted,
                updated=updated,
            )
            return {
                "ok": False,
                "status": "failed",
                "code": error_code,
                "message": "kaspi orders sync failed",
                "company_id": company_id,
                "duration_ms": duration_ms,
            }
        except (TimeoutError, asyncio.TimeoutError):
            duration_ms = int((perf_counter() - started_at) * 1000)

            # Critical fix: explicit rollback before creating fresh session to avoid deadlock
            try:
                if db.in_transaction():
                    await db.rollback()
            except Exception:
                logger.exception("Failed to rollback after timeout: company_id=%s", company_id)

            await self._record_timeout_state(
                db=db,
                company_id=company_id,
                attempt_at=attempt_at,
                duration_ms=duration_ms,
                fetched=fetched,
                inserted=inserted,
                updated=updated,
            )
            logger.error(
                "Kaspi orders sync timeout: company_id=%s request_id=%s duration_ms=%s",
                company_id,
                request_id,
                duration_ms,
            )
            return {
                "ok": False,
                "status": "timeout",
                "code": "timeout",
                "message": "kaspi orders sync timeout",
                "company_id": company_id,
                "duration_ms": duration_ms,
            }
        except Exception as exc:
            duration_ms = int((perf_counter() - started_at) * 1000)
            error_code = self.classify_sync_error(exc)
            error_message = safe_error_message(exc)

            if isinstance(exc, httpx.ConnectTimeout):
                error_code = "connect_timeout"
            elif isinstance(exc, httpx.ReadTimeout):
                error_code = "read_timeout"
            elif isinstance(exc, httpx.TimeoutException):
                error_code = "timeout"

            if isinstance(exc, httpx.TimeoutException):
                req = getattr(exc, "request", None)
                url = getattr(req, "url", None)
                if url is not None:
                    error_message = f"{exc.__class__.__name__} {url}"
                else:
                    error_message = f"{exc.__class__.__name__}"

            await self.record_sync_error(
                db,
                company_id=company_id,
                code=error_code,
                message=error_message,
                occurred_at=_utcnow(),
                attempt_at=attempt_at,
                duration_ms=duration_ms,
                fetched=fetched,
                inserted=inserted,
                updated=updated,
            )
            logger.exception(
                "Kaspi orders sync internal error: company_id=%s request_id=%s duration_ms=%s",
                company_id,
                request_id,
                duration_ms,
            )
            return {
                "ok": False,
                "status": "failed",
                "code": error_code or "failed",
                "message": "kaspi orders sync failed",
                "company_id": company_id,
                "duration_ms": duration_ms,
            }

        summary = {
            "ok": True,
            "status": "success",
            "company_id": company_id,
            "fetched": fetched,
            "inserted": inserted,
            "updated": updated,
            "from": effective_from.isoformat(),
            "to": effective_to.isoformat(),
            "watermark": (state.last_synced_at or final_wm).isoformat() if (state.last_synced_at or final_wm) else None,
        }

        logger.info(
            "Kaspi orders sync done: company_id=%s request_id=%s duration_ms=%s fetched=%s inserted=%s updated=%s",
            company_id,
            request_id,
            int((perf_counter() - started_at) * 1000),
            fetched,
            inserted,
            updated,
        )
        if _diag_enabled():
            logger.info(
                "[CI_DIAG] sync_orders EXIT: company_id=%s request_id=%s fetched=%s inserted=%s updated=%s monotonic=%s",
                company_id,
                request_id,
                fetched,
                inserted,
                updated,
                perf_counter(),
            )
        return summary

    async def _upsert_order_items(
        self,
        db: AsyncSession,
        *,
        order_id: int,
        company_id: int,
        payload: dict[str, Any],
    ) -> bool:
        items = payload.get("items") or payload.get("orderItems") or []
        if not items:
            return False

        now = _utcnow()
        processed = False

        for item in items:
            sku = _as_str(item.get("productSku") or item.get("sku")).strip()
            if not sku:
                continue

            name = _as_str(item.get("productName") or item.get("title") or item.get("name") or sku).strip() or sku

            qty_raw = item.get("quantity") or item.get("qty") or 1
            try:
                qty = max(1, int(qty_raw))
            except Exception:
                qty = 1

            unit_price = self._decimal_or_zero(
                item.get("basePrice") or item.get("unitPrice") or item.get("unit_price") or item.get("price") or 0
            )

            total_price_raw = item.get("totalPrice") or item.get("total_price")
            if total_price_raw is None:
                total_price_raw = unit_price * qty
            total_price = self._decimal_or_zero(total_price_raw)

            cost_price = self._decimal_or_zero(item.get("costPrice") or item.get("cost_price") or 0)

            product_id = None
            product_ext_id = _as_str(item.get("productId") or item.get("product_id") or "").strip()
            if product_ext_id:
                try:
                    res = await db.execute(
                        select(Product.id).where(
                            and_(Product.company_id == company_id, Product.kaspi_product_id == product_ext_id)
                        )
                    )
                    product_id = res.scalar_one_or_none()
                except Exception:
                    product_id = None

            insert_values = {
                "order_id": order_id,
                "product_id": product_id,
                "sku": sku,
                "name": name,
                "unit_price": unit_price,
                "quantity": qty,
                "total_price": total_price,
                "cost_price": cost_price,
            }

            update_values = {
                "product_id": product_id,
                "name": name,
                "unit_price": unit_price,
                "quantity": qty,
                "total_price": total_price,
                "cost_price": cost_price,
            }
            if hasattr(OrderItem, "updated_at"):
                update_values["updated_at"] = now

            stmt = (
                insert(OrderItem)
                .values(**insert_values)
                .on_conflict_do_update(index_elements=[OrderItem.order_id, OrderItem.sku], set_=update_values)
            )

            await db.execute(stmt)
            processed = True

        return processed

    async def _recalculate_order_totals(self, db: AsyncSession, *, order_id: int) -> None:
        res = await db.execute(select(Order).options(selectinload(Order.items)).where(Order.id == order_id))
        order = res.scalar_one_or_none()
        if not order:
            return

        try:
            order.calculate_totals()
        except Exception:
            logger.exception("Kaspi: failed to recalc totals for order_id=%s", order_id)

    async def _upsert_status_history(
        self,
        db: AsyncSession,
        *,
        order_id: int,
        new_status: str | OrderStatus,
        changed_at: datetime | None,
    ) -> None:
        if not changed_at:
            return

        status_enum: OrderStatus
        if isinstance(new_status, OrderStatus):
            status_enum = new_status
        else:
            try:
                status_enum = OrderStatus(new_status)
            except Exception:
                status_enum = OrderStatus.PENDING

        stmt = (
            insert(OrderStatusHistory)
            .values(order_id=order_id, old_status=status_enum, new_status=status_enum, changed_at=changed_at)
            .on_conflict_do_nothing(
                index_elements=[
                    OrderStatusHistory.order_id,
                    OrderStatusHistory.new_status,
                    OrderStatusHistory.changed_at,
                ]
            )
        )

        await db.execute(stmt)

    async def _iter_orders_pages(
        self,
        *,
        date_from: datetime,
        date_to: datetime,
        status: str | None,
        page_size: int,
        company_id: int,
    ) -> AsyncIterator[list[dict[str, Any]]]:
        page = 1

        while True:
            batch = await self._fetch_orders_page(
                date_from=date_from,
                date_to=date_to,
                status=status,
                page=page,
                page_size=page_size,
                company_id=company_id,
            )

            items, meta = self._normalize_orders_response(batch, page, page_size)
            if not items:
                break

            yield items

            next_page = self._next_page(meta=meta, current_page=page, page_size=page_size, items_count=len(items))
            if not next_page:
                break
            page = next_page

    async def _fetch_orders_page(
        self,
        *,
        date_from: datetime,
        date_to: datetime,
        status: str | None,
        page: int,
        page_size: int,
        company_id: int,
    ) -> dict[str, Any] | list[dict[str, Any]]:
        attempts = 3
        delays = [0.2, 0.5, 1.0]
        for attempt in range(attempts):
            try:
                if _diag_enabled():
                    logger.info(
                        "[CI_DIAG] _fetch_orders_page PRE get_orders: company_id=%s page=%s status=%s attempt=%s timeout=%s monotonic=%s",
                        company_id,
                        page,
                        status,
                        attempt + 1,
                        self._orders_timeout(),
                        perf_counter(),
                    )
                result = await self.get_orders(
                    date_from=date_from,
                    date_to=date_to,
                    status=status,
                    page=page,
                    page_size=page_size,
                )
                if _diag_enabled():
                    logger.info(
                        "[CI_DIAG] _fetch_orders_page POST get_orders SUCCESS: company_id=%s page=%s items=%s monotonic=%s",
                        company_id,
                        page,
                        len(result.get("items", []))
                        if isinstance(result, dict)
                        else len(result)
                        if isinstance(result, list)
                        else 0,
                        perf_counter(),
                    )
                return result
            except (
                asyncio.TimeoutError,
                httpx.TimeoutException,
                httpx.NetworkError,
                httpx.HTTPStatusError,
                RuntimeError,
            ) as e:
                if _diag_enabled():
                    logger.error(
                        "[CI_DIAG] _fetch_orders_page EXCEPTION: company_id=%s page=%s attempt=%s exc=%s monotonic=%s",
                        company_id,
                        page,
                        attempt + 1,
                        type(e).__name__,
                        perf_counter(),
                    )
                is_last = attempt == attempts - 1
                code = getattr(getattr(e, "response", None), "status_code", None)
                transient = code in {429, 500, 502, 503, 504} or isinstance(
                    e, asyncio.TimeoutError | httpx.TimeoutException | httpx.NetworkError
                )
                if not transient or is_last:
                    logger.error("Kaspi get_orders failed after retries: %s", e)
                    raise
                retry_after_header = None
                resp_obj = getattr(e, "response", None)
                if isinstance(e, httpx.HTTPStatusError) and code == 429 and resp_obj is not None:
                    try:
                        retry_after_header = resp_obj.headers.get("Retry-After")
                    except Exception:
                        retry_after_header = None
                delay = delays[attempt]
                try:
                    if retry_after_header is not None:
                        delay = max(delay, float(retry_after_header))
                except Exception:
                    delay = delay
                delay = delay + random.uniform(0, 0.2)
                correlation_id = None
                if resp_obj is not None:
                    try:
                        correlation_id = resp_obj.headers.get("X-Correlation-ID")
                    except Exception:
                        correlation_id = None
                logger.warning(
                    "Kaspi get_orders transient code=%s attempt=%s delay=%.2fs page=%s company_id=%s corr=%s",
                    code or type(e).__name__,
                    attempt + 1,
                    delay,
                    page,
                    company_id,
                    correlation_id,
                )
                await asyncio.sleep(delay)

        # Not reachable
        return []

    @staticmethod
    def _normalize_orders_response(
        resp: dict[str, Any] | list[dict[str, Any]] | None, page: int, page_size: int
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        if resp is None:
            return [], {}

        if isinstance(resp, list):
            return resp, {"page": page, "page_size": page_size}

        items = resp.get("items") or resp.get("orders") or resp.get("data") or []
        meta = {
            "page": resp.get("page") or resp.get("pageNumber") or resp.get("page_number") or page,
            "total_pages": _first_present(resp, "total_pages", "totalPages", "pageCount"),
            "has_next": _first_present(resp, "has_next", "hasNext"),
            "next_page": _first_present(resp, "next_page", "nextPage"),
            "links": resp.get("links") or {},
            "page_size": page_size,
        }
        return items, meta

    @staticmethod
    def _next_page(*, meta: dict[str, Any], current_page: int, page_size: int, items_count: int) -> int | None:
        if items_count == 0:
            return None

        raw_next = meta.get("next_page") if "next_page" in meta else None
        if raw_next not in (None, 0, ""):
            try:
                next_page = int(raw_next)
            except (TypeError, ValueError):
                next_page = None
            if next_page is not None and next_page > current_page:
                return next_page
            return None

        if "has_next" in meta:
            has_next = meta.get("has_next")
            if has_next is True:
                return current_page + 1
            if has_next is False:
                return None

        if "total_pages" in meta:
            total_pages = meta.get("total_pages")
            try:
                total_pages_int = int(total_pages) if total_pages is not None else None
            except (TypeError, ValueError):
                total_pages_int = None
            if total_pages_int is not None:
                return current_page + 1 if current_page < total_pages_int else None

        links = meta.get("links") or {}
        if links.get("next"):
            return current_page + 1

        return current_page + 1 if items_count >= page_size else None

    async def _create_order_from_kaspi(
        self, kaspi_order: dict[str, Any], company_id: int, db: AsyncSession
    ) -> Order | None:
        """
        Создаёт Order + OrderItem[] из данных Kaspi.
        Ожидаем, что OrderItem.calculate_total() и Order.calculate_totals() реализованы.
        """
        try:
            ext_id = _as_str(kaspi_order.get("id"))
            order = Order(
                company_id=company_id,
                order_number=f"KASPI-{ext_id}",
                external_id=ext_id,
                source="kaspi",
                status=self._map_kaspi_status(_as_str(kaspi_order.get("status"))),
                customer_phone=(kaspi_order.get("customer") or {}).get("phone"),
                customer_name=(kaspi_order.get("customer") or {}).get("name"),
                customer_address=kaspi_order.get("deliveryAddress"),
                delivery_method=kaspi_order.get("deliveryMode"),
                total_amount=kaspi_order.get("totalPrice", 0),
                currency="KZT",
            )
            db.add(order)
            await db.flush()  # получить order.id

            subtotal = 0
            for it in kaspi_order.get("items", []) or []:
                sku = _as_str(it.get("productSku"))
                name = _as_str(it.get("productName"))
                unit_price = it.get("basePrice", 0)
                qty = int(it.get("quantity", 1))

                # Пытаемся найти локальный продукт по kaspi_product_id
                product: Product | None = None
                try:
                    res = await db.execute(
                        select(Product).where(
                            and_(
                                Product.company_id == company_id,
                                Product.kaspi_product_id == _as_str(it.get("productId")),
                            )
                        )
                    )
                    product = res.scalar_one_or_none()
                except Exception:
                    product = None

                oi = OrderItem(
                    order_id=order.id,
                    product_id=(product.id if product else None),
                    sku=sku,
                    name=name,
                    unit_price=unit_price,
                    quantity=qty,
                )
                if hasattr(oi, "calculate_total"):
                    oi.calculate_total()
                db.add(oi)
                subtotal += getattr(oi, "total_price", unit_price * qty)

            # Финальные суммы
            if hasattr(order, "subtotal"):
                order.subtotal = subtotal
            if hasattr(order, "calculate_totals"):
                order.calculate_totals()
            return order
        except Exception as e:
            logger.error("Failed to create order from Kaspi data: %s", e)
            return None

    def _map_kaspi_status(self, kaspi_status: str) -> str:
        mapping = {
            "NEW": "pending",
            "CONFIRMED": "confirmed",
            "PROCESSING": "processing",
            "SHIPPED": "shipped",
            "DELIVERED": "delivered",
            "COMPLETED": "completed",
            "CANCELLED": "cancelled",
            "RETURNED": "refunded",
        }
        return mapping.get(kaspi_status.upper(), "pending")

    def _company_lock_key(self, company_id: int) -> int:
        raw = f"kaspi-sync-{company_id}".encode()
        h = int.from_bytes(hashlib.sha1(raw).digest()[:8], "big", signed=False)
        return h % (2**63 - 1)

    @asynccontextmanager
    async def _company_lock(self, db: AsyncSession, company_id: int):
        lock_key = self._company_lock_key(company_id)
        res = await db.execute(text("SELECT pg_try_advisory_xact_lock(:lock_key)").bindparams(lock_key=lock_key))
        ok = res.scalar_one_or_none()
        if not ok:
            raise KaspiSyncAlreadyRunning("kaspi sync already running")
        yield

    def _classify_sync_error(self, exc: Exception) -> str:
        if isinstance(exc, asyncio.TimeoutError | TimeoutError):
            return "timeout"
        if isinstance(exc, httpx.TimeoutException):
            return "kaspi_timeout"
        if isinstance(exc, httpx.HTTPStatusError):
            try:
                status = exc.response.status_code
                return f"kaspi_http_{status}"
            except Exception:
                return "kaspi_http_error"
        if isinstance(exc, httpx.HTTPError):
            return "kaspi_http_error"
        if isinstance(exc, KaspiAdapterError):
            return "kaspi_adapter_error"
        return "internal_error"

    def classify_sync_error(self, exc: Exception) -> str:
        return self._classify_sync_error(exc)

    def get_retry_after_seconds(self, exc: Exception) -> int | None:
        if isinstance(exc, httpx.HTTPStatusError):
            try:
                value = exc.response.headers.get("Retry-After")
                if value is None:
                    return None
                return int(value)
            except Exception:
                return None
        return None

    async def _persist_sync_state(
        self,
        db: AsyncSession,
        *,
        company_id: int,
        last_attempt_at: datetime | None = None,
        last_duration_ms: int | None = None,
        last_result: str | None = None,
        last_fetched: int | None = None,
        last_inserted: int | None = None,
        last_updated: int | None = None,
        error_at: datetime | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
        clear_error: bool = False,
    ) -> None:
        if db.in_transaction():
            try:
                await db.rollback()
            except Exception as e:
                logger.debug(
                    "Rollback failed in _update_state (non-critical)",
                    company_id=company_id,
                    error=str(e),
                )
        async with db.begin():
            state = await self._load_or_create_state(db, company_id)
            if last_attempt_at is not None:
                state.last_attempt_at = last_attempt_at
            if last_duration_ms is not None:
                state.last_duration_ms = last_duration_ms
            if last_result is not None:
                state.last_result = last_result
            if last_fetched is not None:
                state.last_fetched = last_fetched
            if last_inserted is not None:
                state.last_inserted = last_inserted
            if last_updated is not None:
                state.last_updated = last_updated

            if clear_error:
                state.last_error_at = None
                state.last_error_code = None
                state.last_error_message = None
            else:
                if error_at is not None:
                    state.last_error_at = error_at
                if error_code is not None:
                    state.last_error_code = error_code
                if error_message is not None:
                    state.last_error_message = error_message[:500]

            state.updated_at = _utcnow()

    async def record_sync_error(
        self,
        db: AsyncSession,
        *,
        company_id: int,
        code: str,
        message: str | Exception,
        occurred_at: datetime,
        attempt_at: datetime | None = None,
        duration_ms: int | None = None,
        fetched: int | None = None,
        inserted: int | None = None,
        updated: int | None = None,
        result: str = "failure",
    ) -> None:
        safe_msg = safe_error_message(message) if isinstance(message, Exception) else str(message or "")[:500]
        await self._persist_sync_state(
            db,
            company_id=company_id,
            last_attempt_at=attempt_at or occurred_at,
            last_duration_ms=duration_ms,
            last_result=result,
            last_fetched=fetched,
            last_inserted=inserted,
            last_updated=updated,
            error_at=occurred_at,
            error_code=code,
            error_message=safe_msg,
        )

    async def _record_timeout_state(
        self,
        *,
        db: AsyncSession,
        company_id: int,
        attempt_at: datetime,
        duration_ms: int,
        fetched: int | None,
        inserted: int | None,
        updated: int | None,
    ) -> None:
        try:
            # Critical fix: timeout on fresh session creation to prevent deadlock
            async with asyncio.timeout(5.0):
                engine = _get_async_engine()
                session_maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
                async with session_maker() as fresh_db:
                    await self.record_sync_error(
                        fresh_db,
                        company_id=company_id,
                        code="timeout",
                        message="kaspi orders sync timeout",
                        occurred_at=_utcnow(),
                        attempt_at=attempt_at,
                        duration_ms=duration_ms,
                        fetched=fetched,
                        inserted=inserted,
                        updated=updated,
                        result="failed",
                    )
                    return
        except asyncio.TimeoutError:
            logger.error(
                "Timeout while recording timeout state (fresh session deadlock?): company_id=%s",
                company_id,
            )
            return
        except Exception:
            logger.exception(
                "Kaspi orders sync: failed to persist timeout state via fresh session: company_id=%s",
                company_id,
            )

        try:
            await self.record_sync_error(
                db,
                company_id=company_id,
                code="timeout",
                message="kaspi orders sync timeout",
                occurred_at=_utcnow(),
                attempt_at=attempt_at,
                duration_ms=duration_ms,
                fetched=fetched,
                inserted=inserted,
                updated=updated,
                result="failed",
            )
        except Exception:
            logger.exception(
                "Kaspi orders sync: fallback timeout state persistence failed: company_id=%s",
                company_id,
            )

    async def record_sync_locked(
        self,
        db: AsyncSession,
        *,
        company_id: int,
        attempt_at: datetime,
        duration_ms: int,
    ) -> None:
        await self._persist_sync_state(
            db,
            company_id=company_id,
            last_attempt_at=attempt_at,
            last_duration_ms=duration_ms,
            last_result="locked",
        )

    async def _acquire_company_lock(self, db: AsyncSession, company_id: int) -> None:
        # Legacy helper preserved for compatibility; delegates to session-level advisory lock
        lock_key = self._company_lock_key(company_id)
        res = await db.execute(text("SELECT pg_try_advisory_xact_lock(:lock_key)").bindparams(lock_key=lock_key))
        ok = res.scalar_one_or_none()
        if not ok:
            raise KaspiSyncAlreadyRunning("kaspi sync already running")

    async def check_lock_available(self, db: AsyncSession, company_id: int) -> bool:
        """Returns True if lock can be acquired within a short transaction."""
        lock_key = self._company_lock_key(company_id)
        from sqlalchemy.ext.asyncio import AsyncEngine

        try:
            engine: AsyncEngine = db.get_bind()  # type: ignore
            # Create truly fresh connection by disposing and getting new one
            async with engine.connect() as conn:
                # Start a new transaction and try to acquire lock
                async with conn.begin():
                    res = await conn.execute(
                        text("SELECT pg_try_advisory_xact_lock(:lock_key)").bindparams(lock_key=lock_key)
                    )
                    ok = res.scalar_one_or_none()
                    # Lock is automatically released at end of transaction
                    return bool(ok)
        except Exception:
            return False

    async def _load_or_create_state(self, db: AsyncSession, company_id: int) -> KaspiOrderSyncState:
        q = await db.execute(
            select(KaspiOrderSyncState).where(KaspiOrderSyncState.company_id == company_id).with_for_update()
        )
        state = q.scalar_one_or_none()
        if not state:
            state = KaspiOrderSyncState(company_id=company_id)
            db.add(state)
            await db.flush()
        return state

    def _order_number_from_payload(self, company_id: int, external_id: str, payload: dict[str, Any]) -> str:
        candidate = _as_str(payload.get("orderNumber") or payload.get("code") or "").strip()
        if candidate:
            return candidate
        return f"KASPI-{company_id}-{external_id}"

    def _extract_order_timestamp(self, payload: dict[str, Any]) -> datetime | None:
        candidates = [
            payload.get("updated_at"),
            payload.get("updatedAt"),
            payload.get("updated"),
            payload.get("modificationDate"),
            payload.get("modified_at"),
            payload.get("changedAt"),
            payload.get("created_at"),
            payload.get("createdAt"),
        ]
        for candidate in candidates:
            ts = self._parse_dt(candidate)
            if ts:
                return ts
        return None

    @staticmethod
    def _parse_dt(value: Any) -> datetime | None:
        if isinstance(value, datetime):
            return value.astimezone(UTC).replace(tzinfo=None) if value.tzinfo else value
        if isinstance(value, str):
            try:
                cleaned = value.replace("Z", "+00:00")
                dt = datetime.fromisoformat(cleaned)
                return dt.astimezone(UTC).replace(tzinfo=None) if dt.tzinfo else dt
            except Exception:
                return None
        return None

    @staticmethod
    def _decimal_or_zero(value: Any):
        try:
            return Decimal(str(value or 0))
        except Exception:
            return Decimal("0")

    # ---------------------- Product feed ---------------------- #

    async def generate_product_feed(self, company_id: int, db: AsyncSession) -> str:
        """
        Генерация XML-фида по активным товарам.
        Включаем товары: company_id, is_active=True, deleted_at IS NULL.
        """
        try:
            result = await db.execute(
                select(Product).where(
                    and_(
                        Product.company_id == company_id,
                        Product.is_active.is_(True),
                        Product.deleted_at.is_(None),
                    )
                )
            )
            products: list[Product] = list(result.scalars().all())
            xml_content = self._generate_xml_feed(products)
            logger.info("Kaspi: сгенерирован фид из %s товаров.", len(products))
            return xml_content
        except Exception as e:
            logger.error("Kaspi generate_product_feed error: %s", e)
            raise RuntimeError(f"Failed to generate product feed: {e}") from e

    def _generate_xml_feed(self, products: list[Product]) -> str:
        """
        Поля фида подогнаны под наши модели:
          - id: kaspi_product_id или наш id
          - sku, name, description
          - price: product.current_price (или 0.00)
          - category: Product.get_category_path()
          - brand: из Product.extra["brand"] либо пусто
          - availability: free_stock (если предзаказ — 0)
          - image: image_url
        """
        lines: list[str] = ['<?xml version="1.0" encoding="UTF-8"?>', "<products>"]

        for p in products:
            pid = _as_str(getattr(p, "kaspi_product_id", None) or p.id)
            sku = _as_str(getattr(p, "sku", ""))
            name = _as_str(getattr(p, "name", ""))
            desc = _as_str(getattr(p, "description", "") or "")
            price = getattr(p, "current_price", None)
            price_val = price if isinstance(price, int | float) else 0
            price_str = f"{float(price_val):.2f}"

            category_path = ""
            try:
                if hasattr(p, "get_category_path"):
                    category_path = _as_str(p.get_category_path())
            except Exception:
                category_path = ""

            brand = self._extract_brand(p)

            free_stock = getattr(p, "free_stock", 0) or 0
            is_preorder = False
            try:
                if hasattr(p, "is_preorder"):
                    is_preorder = bool(p.is_preorder())
            except Exception:
                is_preorder = False
            availability = 0 if is_preorder else max(0, int(free_stock))

            image = _as_str(getattr(p, "image_url", "") or "")

            lines.extend(
                [
                    "  <product>",
                    f"    <id>{_xml_escape(pid)}</id>",
                    f"    <sku>{_xml_escape(sku)}</sku>",
                    f"    <name>{_cdata(name)}</name>",
                    f"    <description>{_cdata(desc)}</description>",
                    f"    <price>{_xml_escape(price_str)}</price>",
                    f"    <category>{_cdata(category_path)}</category>",
                    f"    <brand>{_cdata(brand)}</brand>",
                    f"    <availability>{availability}</availability>",
                    f"    <image>{_xml_escape(image)}</image>",
                    "  </product>",
                ]
            )

        lines.append("</products>")
        return "\n".join(lines)

    def _extract_brand(self, p: Product) -> str:
        """
        Пытаемся взять бренд из Product.extra["brand"] или Product.extra["repricing"]["brand"].
        Если нет — вернём пустую строку.
        """
        try:
            if hasattr(p, "get_extra"):
                extra = p.get_extra()
            else:
                extra = getattr(p, "extra", None)

            if isinstance(extra, dict):
                if extra.get("brand"):
                    return str(extra["brand"])
                rp = extra.get("repricing")
                if isinstance(rp, dict) and rp.get("brand"):
                    return str(rp["brand"])
        except Exception as e:
            logger.debug(
                "Brand extraction failed for product",
                product_id=getattr(p, "id", None),
                error=str(e),
            )
        return ""

    # ---------------------- Availability sync ---------------------- #

    async def sync_product_availability(self, product: Product) -> bool:
        """
        Апдейт доступности конкретного товара на стороне Kaspi.
        Если kaspi_product_id отсутствует — пропускаем (True), чтобы не ронять пайплайн.
        """
        kaspi_product_id = _as_str(getattr(product, "kaspi_product_id", None) or "")
        if not kaspi_product_id:
            logger.info(
                "Kaspi availability: пропуск, у товара %s нет kaspi_product_id.",
                getattr(product, "id", "?"),
            )
            return True

        free_stock = getattr(product, "free_stock", 0) or 0
        is_preorder = False
        try:
            if hasattr(product, "is_preorder"):
                is_preorder = bool(product.is_preorder())
        except Exception:
            is_preorder = False

        availability = 0 if is_preorder else max(0, int(free_stock))
        return await self.update_product_availability(kaspi_product_id, availability)

    async def bulk_sync_availability(self, company_id: int, db: AsyncSession, *, limit: int = 500) -> dict[str, int]:
        """
        Массовый апдейт доступности в Kaspi для активных товаров компании.
        """
        result = await db.execute(
            select(Product).where(
                and_(
                    Product.company_id == company_id,
                    Product.is_active.is_(True),
                    Product.deleted_at.is_(None),
                )
            )
        )
        products: list[Product] = list(result.scalars().all())[: max(0, limit)]
        ok = 0
        fail = 0

        for p in products:
            try:
                if await self.sync_product_availability(p):
                    ok += 1
                else:
                    fail += 1
            except Exception as e:
                logger.error("Kaspi bulk availability error for product %s: %s", getattr(p, "id", "?"), e)
                fail += 1

        stats = {"total": len(products), "ok": ok, "fail": fail}
        logger.info("Kaspi bulk availability sync completed: %s", stats)
        return stats
