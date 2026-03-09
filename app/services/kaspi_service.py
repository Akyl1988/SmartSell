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
import inspect
import json
import os
import random
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, nullcontext
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from time import perf_counter
from typing import Any, Optional

import anyio  # noqa: F401
import httpx
from sqlalchemy import and_, literal_column, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.core.db import _get_async_engine
from app.core.errors import safe_error_message
from app.core.logging import get_logger
from app.integrations.kaspi_adapter import KaspiAdapterError
from app.models import Order, OrderItem, Product
from app.models.kaspi_catalog_item import KaspiCatalogItem
from app.models.kaspi_order_sync_state import KaspiOrderSyncState
from app.models.order import OrderSource, OrderStatus, OrderStatusHistory
from app.models.preorder import Preorder, PreorderItem, PreorderStatus
from app.services.kaspi_service_feed import KaspiServiceFeedMixin
from app.services.kaspi_service_http import KaspiServiceHttpMixin
from app.services.preorders import cancel_preorder, confirm_preorder, fulfill_preorder

logger = get_logger(__name__)


class KaspiSyncAlreadyRunning(RuntimeError):
    pass


class KaspiBadRequestError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class KaspiProductsUpstreamError(RuntimeError):
    def __init__(self, code: str, *, status_code: int | None = None):
        super().__init__(code)
        self.code = code
        self.status_code = status_code


from app.services.kaspi_service_transport import (
    _safe_httpx_request,
    _safe_httpx_response,
)
from app.services.kaspi_service_utils import (
    DEFAULT_KASPI_ORDER_STATES,
    _as_str,
    _diag_enabled,
    _epoch_ms_to_utc_iso,
    _extract_kaspi_order_attrs,
    _first_present,
    _merge_kaspi_internal_notes,
    _normalize_address,
    _normalize_kaspi_base_url,
    _parse_kaspi_states,
    _utcnow,
)

# ---------------------- main service ---------------------- #


class KaspiService(KaspiServiceHttpMixin, KaspiServiceFeedMixin):
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
        raw_base_url = (base_url or getattr(settings, "KASPI_API_URL", "") or "").rstrip("/")
        self.base_url = _normalize_kaspi_base_url(raw_base_url)
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

    # ---------------------- Orders sync (DB) ---------------------- #

    async def sync_orders(
        self,
        *,
        db: AsyncSession,
        company_id: int,
        merchant_uid: str | None = None,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
        statuses: list[str] | None = None,
        request_id: str | None = None,
        timeout_seconds: float | None = None,
        max_pages: int | None = None,
        max_window_minutes: int | None = None,
        backfill_days: int | None = None,
        orders_max_attempts: int | None = None,
        client_retries: int | None = None,
    ) -> dict[str, Any]:
        """Инкрементальная и идемпотентная синхронизация заказов Kaspi."""
        attempt_at = _utcnow()
        effective_to = date_to or attempt_at
        overlap = timedelta(minutes=2)
        fetched = 0
        inserted = 0
        updated = 0
        started_at = perf_counter()
        timeout_seconds = float(timeout_seconds or self._sync_timeout_seconds or 30)
        max_pages = None if max_pages is None else max(1, int(max_pages))
        max_window_minutes = None if max_window_minutes is None else max(1, int(max_window_minutes))
        pagination_state = {
            "pages_processed": 0,
            "last_page": 0,
            "stopped_early": False,
            "page_limit_hit": False,
            "window_truncated": False,
        }
        backfill_days_value = int(backfill_days or 0)
        backfill_active = backfill_days_value > 0

        timeout_obj = self._orders_timeout(timeout_seconds)
        logger.info(
            "Kaspi orders sync timeout budget: company_id=%s request_id=%s timeout_sec=%s connect=%s read=%s write=%s pool=%s",
            company_id,
            request_id,
            timeout_seconds,
            getattr(timeout_obj, "connect", None),
            getattr(timeout_obj, "read", None),
            getattr(timeout_obj, "write", None),
            getattr(timeout_obj, "pool", None),
        )

        logger.info("Kaspi orders sync start: company_id=%s request_id=%s", company_id, request_id)
        if _diag_enabled():
            logger.info(
                "[CI_DIAG] sync_orders ENTRY: company_id=%s request_id=%s timeout=%s backfill_days=%s backfill_active=%s monotonic=%s",
                company_id,
                request_id,
                timeout_seconds,
                backfill_days_value,
                backfill_active,
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
                        sync_state = await self._load_or_create_state(db, company_id)

                        sync_state.last_attempt_at = attempt_at
                        sync_state.last_result = None
                        sync_state.last_duration_ms = None
                        sync_state.last_fetched = None
                        sync_state.last_inserted = None
                        sync_state.last_updated = None

                        prev_last_synced = sync_state.last_synced_at
                        prev_last_ext = sync_state.last_external_order_id
                        is_new_state = prev_last_synced is None and prev_last_ext is None

                        if backfill_active and date_from is None:
                            base_from = effective_to - timedelta(days=backfill_days_value)
                        else:
                            base_from = date_from or prev_last_synced or (effective_to - timedelta(days=1))
                        min_from = (
                            effective_to - timedelta(minutes=max_window_minutes)
                            if max_window_minutes is not None
                            else None
                        )
                        if min_from is not None and base_from < min_from:
                            pagination_state["stopped_early"] = True
                            pagination_state["window_truncated"] = True
                        effective_from = base_from - overlap
                        if min_from is not None and effective_from < min_from:
                            effective_from = min_from

                        if backfill_active:
                            logger.info(
                                "Kaspi orders sync backfill: company_id=%s request_id=%s backfill_days=%s from=%s to=%s prev_last_synced=%s",
                                company_id,
                                request_id,
                                backfill_days_value,
                                effective_from.isoformat(),
                                effective_to.isoformat(),
                                prev_last_synced.isoformat() if prev_last_synced else None,
                            )

                        watermark = prev_last_synced or base_from
                        last_ext = prev_last_ext
                        made_progress = False

                        state_filters = self._resolve_order_states(statuses)
                        for order_state in state_filters:
                            async for batch in self._iter_orders_pages(
                                date_from=effective_from,
                                date_to=effective_to,
                                state=order_state,
                                page_size=100,
                                company_id=company_id,
                                merchant_uid=merchant_uid,
                                request_id=request_id,
                                max_pages=max_pages,
                                pagination_state=pagination_state,
                                orders_timeout_sec=timeout_seconds,
                                max_attempts=orders_max_attempts,
                                client_retries=client_retries,
                            ):
                                catalog_rows_map: dict[tuple[str, str], dict[str, Any]] = {}
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
                                    delivery_address = _normalize_address(
                                        payload.get("deliveryAddress") or payload.get("delivery_address")
                                    )
                                    kaspi_attrs = _extract_kaspi_order_attrs(payload)
                                    planned_date = (
                                        payload.get("plannedDeliveryDate") if "plannedDeliveryDate" in payload else None
                                    )
                                    reservation_date = (
                                        payload.get("reservationDate") if "reservationDate" in payload else None
                                    )
                                    delivery_date = _epoch_ms_to_utc_iso(planned_date)
                                    if delivery_date is None:
                                        delivery_date = _epoch_ms_to_utc_iso(reservation_date)
                                    status_changed_at = self._extract_order_timestamp(payload)
                                    updated_ts = status_changed_at or effective_to
                                    effective_updated = updated_ts or attempt_at

                                    if merchant_uid:
                                        for row in self._build_catalog_rows(
                                            company_id=company_id,
                                            merchant_uid=merchant_uid,
                                            payload=payload,
                                            last_seen_at=effective_updated,
                                        ):
                                            key = (row["merchant_uid"], row["sku"])
                                            existing = catalog_rows_map.get(key)
                                            if not existing or row["last_seen_at"] >= existing.get("last_seen_at"):
                                                catalog_rows_map[key] = row

                                    update_values = {
                                        "status": mapped_status,
                                        "source": OrderSource.KASPI,
                                        "order_number": order_number,
                                        "customer_phone": customer.get("phone") or None,
                                        "customer_name": customer.get("name") or None,
                                        "customer_address": delivery_address,
                                        "delivery_method": payload.get("deliveryMode")
                                        or payload.get("delivery_mode")
                                        or None,
                                        "total_amount": total_amount,
                                        "currency": currency,
                                        "updated_at": effective_updated,
                                    }
                                    if delivery_date is not None:
                                        update_values["delivery_date"] = delivery_date

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
                                            customer_address=delivery_address,
                                            delivery_method=payload.get("deliveryMode")
                                            or payload.get("delivery_mode")
                                            or None,
                                            delivery_date=delivery_date,
                                            total_amount=total_amount,
                                            currency=currency,
                                            updated_at=effective_updated,
                                        )
                                        .on_conflict_do_update(
                                            index_elements=[Order.company_id, Order.external_id],
                                            set_=update_values,
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

                                    if kaspi_attrs:
                                        notes_row = await db.execute(
                                            select(Order.internal_notes).where(Order.id == order_pk)
                                        )
                                        merged_notes = _merge_kaspi_internal_notes(
                                            notes_row.scalar_one_or_none(),
                                            kaspi_attrs,
                                        )
                                        await db.execute(
                                            update(Order)
                                            .where(Order.id == order_pk)
                                            .values(
                                                internal_notes=json.dumps(
                                                    merged_notes,
                                                    separators=(",", ":"),
                                                    sort_keys=True,
                                                )
                                            )
                                        )

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

                                    preorder = await self._get_or_create_kaspi_preorder(
                                        db,
                                        company_id=company_id,
                                        payload=payload,
                                    )
                                    if preorder is not None:
                                        await self._apply_kaspi_preorder_transition(
                                            db,
                                            company_id=company_id,
                                            preorder=preorder,
                                            mapped_status=mapped_status,
                                            order_id=order_pk,
                                        )

                                if catalog_rows_map:
                                    await self._upsert_catalog_items(db, list(catalog_rows_map.values()))

                                # page handling happens inside _iter_orders_pages

                            page_limit_hit = bool(pagination_state.get("page_limit_hit"))
                            if (made_progress or is_new_state) and not page_limit_hit:
                                final_wm = watermark if prev_last_synced is None else max(prev_last_synced, watermark)
                                sync_state.last_synced_at = final_wm
                                sync_state.last_external_order_id = last_ext
                            else:
                                final_wm = prev_last_synced
                                sync_state.last_synced_at = prev_last_synced
                                sync_state.last_external_order_id = prev_last_ext

                            if pagination_state.get("stopped_early"):
                                break

                        finished_at = _utcnow()
                        duration_ms = int((perf_counter() - started_at) * 1000)
                        sync_state.updated_at = finished_at
                        sync_state.last_error_at = None
                        sync_state.last_error_code = None
                        sync_state.last_error_message = None
                        if pagination_state.get("no_orders"):
                            sync_state.last_result = "success"
                        else:
                            sync_state.last_result = "partial" if pagination_state.get("stopped_early") else "success"
                        sync_state.last_duration_ms = duration_ms
                        sync_state.last_attempt_at = attempt_at
                        sync_state.last_fetched = fetched
                        sync_state.last_inserted = inserted
                        sync_state.last_updated = updated
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
        except KaspiBadRequestError as exc:
            duration_ms = int((perf_counter() - started_at) * 1000)
            detail = str(exc)
            await self.record_sync_error(
                db,
                company_id=company_id,
                code="KASPI_BAD_REQUEST",
                message=detail,
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
                "code": "KASPI_BAD_REQUEST",
                "message": detail or "kaspi bad request",
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

        no_orders = bool(pagination_state.get("no_orders")) if pagination_state is not None else False
        summary_status = "success" if no_orders else "partial" if pagination_state.get("stopped_early") else "success"
        summary = {
            "ok": True,
            "status": summary_status,
            "company_id": company_id,
            "fetched": fetched,
            "inserted": inserted,
            "updated": updated,
            "from": effective_from.isoformat(),
            "to": effective_to.isoformat(),
            "watermark": (sync_state.last_synced_at or final_wm).isoformat()
            if (sync_state.last_synced_at or final_wm)
            else None,
        }

        if backfill_active:
            summary["backfill_days"] = backfill_days_value

        if pagination_state.get("page_limit_hit") and not no_orders:
            summary["last_page_processed"] = int(pagination_state.get("last_page", 0))
            summary["next_hint"] = "continue"
        if pagination_state.get("page_limit_hit"):
            summary["page_limit_hit"] = True
        if pagination_state.get("window_truncated"):
            summary["window_truncated"] = True

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

    def _extract_catalog_entry_id(self, item: dict[str, Any]) -> str | None:
        candidates = (
            item.get("productSku"),
            item.get("sku"),
            item.get("offerCode"),
            item.get("offer_code"),
            item.get("productCode"),
            item.get("product_code"),
            item.get("productId"),
            item.get("product_id"),
            item.get("code"),
        )
        for value in candidates:
            sku = _as_str(value).strip()
            if sku:
                return sku
        return None

    def _build_catalog_rows(
        self,
        *,
        company_id: int,
        merchant_uid: str,
        payload: dict[str, Any],
        last_seen_at: datetime,
    ) -> list[dict[str, Any]]:
        items = payload.get("items") or payload.get("orderItems") or payload.get("entries") or []
        if not isinstance(items, list):
            return []

        rows: list[dict[str, Any]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            sku = self._extract_catalog_entry_id(item)
            if not sku:
                continue

            offer_code = _as_str(item.get("offerCode") or item.get("offer_code") or "").strip() or None
            product_code = (
                _as_str(
                    item.get("productCode")
                    or item.get("product_code")
                    or item.get("productId")
                    or item.get("product_id")
                    or ""
                ).strip()
                or None
            )
            name = _as_str(item.get("productName") or item.get("title") or item.get("name") or sku).strip() or None

            qty_raw = item.get("quantity") or item.get("qty")
            qty = None
            if qty_raw is not None:
                try:
                    qty = max(0, int(qty_raw))
                except Exception:
                    qty = None

            unit_price = self._decimal_or_zero(
                item.get("basePrice") or item.get("unitPrice") or item.get("unit_price") or item.get("price") or 0
            )
            if not unit_price:
                total_price_raw = item.get("totalPrice") or item.get("total_price")
                if total_price_raw is not None and qty:
                    unit_price = self._decimal_or_zero(total_price_raw) / Decimal(qty)

            rows.append(
                {
                    "company_id": company_id,
                    "merchant_uid": merchant_uid,
                    "sku": sku,
                    "offer_code": offer_code,
                    "product_code": product_code,
                    "last_seen_name": name,
                    "last_seen_price": unit_price,
                    "last_seen_qty": qty,
                    "last_seen_at": last_seen_at,
                    "raw": item,
                }
            )
        return rows

    async def _upsert_catalog_items(self, db: AsyncSession, rows: list[dict[str, Any]]) -> None:
        if not rows:
            return

        now = _utcnow()
        for row in rows:
            row.setdefault("created_at", now)
            row["updated_at"] = now

        stmt = insert(KaspiCatalogItem).values(rows)
        update_values = {
            "offer_code": stmt.excluded.offer_code,
            "product_code": stmt.excluded.product_code,
            "last_seen_name": stmt.excluded.last_seen_name,
            "last_seen_price": stmt.excluded.last_seen_price,
            "last_seen_qty": stmt.excluded.last_seen_qty,
            "last_seen_at": stmt.excluded.last_seen_at,
            "raw": stmt.excluded.raw,
            "updated_at": stmt.excluded.updated_at,
        }
        stmt = stmt.on_conflict_do_update(
            index_elements=[
                KaspiCatalogItem.company_id,
                KaspiCatalogItem.merchant_uid,
                KaspiCatalogItem.sku,
            ],
            set_=update_values,
        )
        await db.execute(stmt)

    async def _build_kaspi_preorder_items(
        self,
        db: AsyncSession,
        *,
        company_id: int,
        payload: dict[str, Any],
    ) -> tuple[list[PreorderItem], Decimal | None]:
        items_payload = payload.get("items") or payload.get("orderItems") or []
        if not items_payload:
            return [], None

        total = Decimal("0.00")
        items: list[PreorderItem] = []

        for item in items_payload:
            sku = _as_str(item.get("productSku") or item.get("sku")).strip()
            name = _as_str(item.get("productName") or item.get("title") or item.get("name") or sku).strip() or sku

            qty_raw = item.get("quantity") or item.get("qty") or 1
            try:
                qty = max(1, int(qty_raw))
            except Exception:
                qty = 1

            unit_price = self._decimal_or_zero(
                item.get("basePrice") or item.get("unitPrice") or item.get("unit_price") or item.get("price") or 0
            )
            if not unit_price:
                total_price_raw = item.get("totalPrice") or item.get("total_price")
                if total_price_raw is not None and qty > 0:
                    unit_price = self._decimal_or_zero(total_price_raw) / Decimal(qty)

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

            items.append(
                PreorderItem(
                    product_id=product_id,
                    sku=sku or None,
                    name=name or None,
                    qty=qty,
                    price=unit_price,
                )
            )
            total += (unit_price or Decimal("0")) * Decimal(qty)

        return items, total.quantize(Decimal("0.01"))

    async def _get_or_create_kaspi_preorder(
        self,
        db: AsyncSession,
        *,
        company_id: int,
        payload: dict[str, Any],
    ) -> Preorder | None:
        raw_external_id = payload.get("id")
        if not isinstance(raw_external_id, str) or not raw_external_id.strip():
            logger.warning(
                "Kaspi preorder skipped: missing order id (company_id=%s, keys=%s)",
                company_id,
                sorted(payload.keys()),
            )
            return None
        external_id = raw_external_id.strip()
        currency = _as_str(payload.get("currency")) or "KZT"
        customer = payload.get("customer") or {}
        notes = _as_str(payload.get("notes") or "").strip() or None

        result = await db.execute(
            select(Preorder)
            .where(
                Preorder.company_id == company_id,
                Preorder.source == OrderSource.KASPI.value,
                Preorder.external_id == external_id,
            )
            .options(selectinload(Preorder.items))
        )
        preorder = result.scalar_one_or_none()

        items, total = await self._build_kaspi_preorder_items(db, company_id=company_id, payload=payload)

        if preorder is None:
            preorder = Preorder(
                company_id=company_id,
                status=PreorderStatus.NEW,
                currency=currency,
                total=total,
                customer_name=customer.get("name") or None,
                customer_phone=customer.get("phone") or None,
                notes=notes,
                source=OrderSource.KASPI.value,
                external_id=external_id,
            )
            preorder.items = items
            db.add(preorder)
            await db.flush()
            return preorder

        if preorder.status == PreorderStatus.NEW:
            preorder.currency = currency or preorder.currency
            preorder.customer_name = customer.get("name") or preorder.customer_name
            preorder.customer_phone = customer.get("phone") or preorder.customer_phone
            preorder.notes = notes or preorder.notes
            preorder.total = total
            preorder.items.clear()
            preorder.items.extend(items)

        return preorder

    async def _apply_kaspi_preorder_transition(
        self,
        db: AsyncSession,
        *,
        company_id: int,
        preorder: Preorder,
        mapped_status: str,
        order_id: int,
    ) -> Preorder:
        status_value = (mapped_status or "").lower()
        confirm_statuses = {
            OrderStatus.CONFIRMED.value,
            OrderStatus.PROCESSING.value,
            OrderStatus.SHIPPED.value,
        }
        fulfill_statuses = {OrderStatus.DELIVERED.value, OrderStatus.COMPLETED.value}
        cancel_statuses = {OrderStatus.CANCELLED.value}

        if preorder.status in {PreorderStatus.CANCELLED, PreorderStatus.FULFILLED}:
            return preorder

        if status_value in cancel_statuses:
            return await cancel_preorder(db, company_id=company_id, preorder_id=preorder.id)

        if status_value in fulfill_statuses:
            if preorder.status == PreorderStatus.NEW:
                preorder = await confirm_preorder(db, company_id=company_id, preorder_id=preorder.id)
            return await fulfill_preorder(
                db,
                company_id=company_id,
                preorder_id=preorder.id,
                existing_order_id=order_id,
            )

        if status_value in confirm_statuses and preorder.status == PreorderStatus.NEW:
            return await confirm_preorder(db, company_id=company_id, preorder_id=preorder.id)

        return preorder

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
        state: str | None,
        page_size: int,
        company_id: int,
        merchant_uid: str | None = None,
        request_id: str | None = None,
        max_pages: int | None = None,
        pagination_state: dict[str, Any] | None = None,
        orders_timeout_sec: float | None = None,
        max_attempts: int | None = None,
        client_retries: int | None = None,
    ) -> AsyncIterator[list[dict[str, Any]]]:
        page = 1

        while True:
            if pagination_state is not None and max_pages is not None:
                if pagination_state.get("pages_processed", 0) >= max_pages:
                    pagination_state["stopped_early"] = True
                    pagination_state["page_limit_hit"] = True
                    break
            batch = await self._fetch_orders_page(
                date_from=date_from,
                date_to=date_to,
                state=state,
                page=page,
                page_size=page_size,
                company_id=company_id,
                merchant_uid=merchant_uid,
                request_id=request_id,
                orders_timeout_sec=orders_timeout_sec,
                max_attempts=max_attempts,
                client_retries=client_retries,
            )

            if pagination_state is not None:
                pagination_state["pages_processed"] = int(pagination_state.get("pages_processed", 0)) + 1
                pagination_state["last_page"] = page

            items, meta = self._normalize_orders_response(batch, page, page_size)
            if not items:
                if pagination_state is not None:
                    total_pages = meta.get("total_pages")
                    try:
                        total_pages_int = int(total_pages) if total_pages is not None else None
                    except (TypeError, ValueError):
                        total_pages_int = None
                    if total_pages_int == 0:
                        pagination_state["no_orders"] = True
                        pagination_state["stopped_early"] = False
                break

            yield items

            next_page = self._next_page(meta=meta, current_page=page, page_size=page_size, items_count=len(items))
            if not next_page:
                break
            if pagination_state is not None and max_pages is not None:
                if pagination_state.get("pages_processed", 0) >= max_pages:
                    pagination_state["stopped_early"] = True
                    pagination_state["page_limit_hit"] = True
                    break
            page = next_page

    async def _fetch_orders_page(
        self,
        *,
        date_from: datetime,
        date_to: datetime,
        state: str | None,
        page: int,
        page_size: int,
        company_id: int,
        merchant_uid: str | None = None,
        request_id: str | None = None,
        orders_timeout_sec: float | None = None,
        max_attempts: int | None = None,
        client_retries: int | None = None,
    ) -> dict[str, Any] | list[dict[str, Any]]:
        attempts = max(1, int(max_attempts or 3))
        delays = [0.2, 0.5, 1.0]
        for attempt in range(attempts):
            try:
                if _diag_enabled():
                    logger.info(
                        "[CI_DIAG] _fetch_orders_page PRE get_orders: company_id=%s page=%s state=%s attempt=%s timeout=%s monotonic=%s",
                        company_id,
                        page,
                        state,
                        attempt + 1,
                        self._orders_timeout(orders_timeout_sec),
                        perf_counter(),
                    )
                    logger.info(
                        "[CI_DIAG] kaspi_orders_attempt",
                        extra={
                            "company_id": company_id,
                            "request_id": request_id,
                            "merchant_uid_present": bool(merchant_uid),
                            "page": page,
                            "attempt": attempt + 1,
                            "max_attempts": attempts,
                        },
                    )
                base_kwargs = {
                    "date_from": date_from,
                    "date_to": date_to,
                    "state": state,
                    "page": page,
                    "page_size": page_size,
                    "company_id": company_id,
                    "merchant_uid": merchant_uid,
                    "request_id": request_id,
                }
                extra_kwargs: dict[str, Any] = {}
                if orders_timeout_sec is not None:
                    extra_kwargs["timeout"] = self._orders_timeout(orders_timeout_sec)
                if client_retries is not None:
                    extra_kwargs["retries"] = client_retries

                call_kwargs = {**base_kwargs, **extra_kwargs}
                call_kwargs = self._filter_get_orders_kwargs(call_kwargs)
                result = await self.get_orders(**call_kwargs)
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
                req_obj = _safe_httpx_request(e)
                resp_obj = _safe_httpx_response(e)
                method = getattr(req_obj, "method", None)
                url = getattr(req_obj, "url", None)
                status_code = getattr(resp_obj, "status_code", None)
                exc_type = type(e).__name__
                exc_repr = repr(e)
                if not transient or is_last:
                    logger.error(
                        "Kaspi get_orders failed after retries: %s | exc_type=%s exc_repr=%s attempt=%s method=%s url=%s status_code=%s",
                        e,
                        exc_type,
                        exc_repr,
                        attempt + 1,
                        method,
                        url,
                        status_code,
                    )
                    raise
                retry_after_header = None
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
                    "Kaspi get_orders transient code=%s attempt=%s delay=%.2fs page=%s company_id=%s corr=%s exc_type=%s exc_repr=%s method=%s url=%s status_code=%s",
                    code or type(e).__name__,
                    attempt + 1,
                    delay,
                    page,
                    company_id,
                    correlation_id,
                    exc_type,
                    exc_repr,
                    method,
                    url,
                    status_code,
                )
                if _diag_enabled():
                    logger.warning(
                        "[CI_DIAG] kaspi_orders_attempt_failed",
                        extra={
                            "company_id": company_id,
                            "request_id": request_id,
                            "merchant_uid_present": bool(merchant_uid),
                            "page": page,
                            "attempt": attempt + 1,
                            "max_attempts": attempts,
                            "exc_type": exc_type,
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
                            "status_code": status_code,
                            "backoff_sec": delay,
                        },
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
                customer_address=_normalize_address(kaspi_order.get("deliveryAddress")),
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
            "ACCEPTED": "confirmed",
            "APPROVED": "confirmed",
            "CONFIRMED": "confirmed",
            "PACKING": "processing",
            "PROCESSING": "processing",
            "SHIPPED": "shipped",
            "DELIVERED": "delivered",
            "COMPLETED": "completed",
            "CANCELLED": "cancelled",
            "CANCELED": "cancelled",
            "RETURNED": "refunded",
            "RETURNED_TO_SHOP": "refunded",
            "RETURNED_TO_SELLER": "refunded",
            "REFUNDED": "refunded",
        }
        normalized = _as_str(kaspi_status).strip().upper()
        return mapping.get(normalized, "pending")

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
        for _ in range(2):
            q = await db.execute(
                select(KaspiOrderSyncState).where(KaspiOrderSyncState.company_id == company_id).with_for_update()
            )
            state = q.scalar_one_or_none()
            if state:
                return state

            stmt = (
                insert(KaspiOrderSyncState)
                .values(company_id=company_id)
                .on_conflict_do_nothing(index_elements=[KaspiOrderSyncState.company_id])
            )
            await db.execute(stmt)
            await db.flush()

        q = await db.execute(select(KaspiOrderSyncState).where(KaspiOrderSyncState.company_id == company_id))
        state = q.scalar_one_or_none()
        if state:
            return state

        state = KaspiOrderSyncState(company_id=company_id)
        db.add(state)
        await db.flush()
        return state

    def _order_number_from_payload(self, company_id: int, external_id: str, payload: dict[str, Any]) -> str:
        candidate = _as_str(payload.get("orderNumber") or payload.get("code") or "").strip()
        if candidate:
            return candidate
        return f"KASPI-{company_id}-{external_id}"

    def _filter_get_orders_kwargs(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        try:
            signature = inspect.signature(self.get_orders)
        except (TypeError, ValueError):
            return kwargs

        params = signature.parameters
        if any(param.kind == inspect.Parameter.VAR_KEYWORD for param in params.values()):
            return kwargs

        filtered = {key: value for key, value in kwargs.items() if key in params}

        if "state" in kwargs and "state" not in filtered and "status" in params:
            filtered["status"] = kwargs.get("state")

        return filtered

    def _resolve_order_states(self, statuses: list[str] | None) -> list[str | None]:
        if statuses:
            resolved = _parse_kaspi_states(statuses)
            return resolved or [None]

        raw_env = os.environ.get("KASPI_ORDERS_SYNC_STATES")
        if raw_env:
            if raw_env.strip().lower() in {"all", "*", "default"}:
                return list(DEFAULT_KASPI_ORDER_STATES)
            resolved = _parse_kaspi_states(raw_env)
            return resolved or [None]

        return [None]

    def _extract_order_timestamp(self, payload: dict[str, Any]) -> datetime | None:
        candidates = [
            payload.get("updated_at"),
            payload.get("updatedAt"),
            payload.get("updated"),
            payload.get("modificationDate"),
            payload.get("modified_at"),
            payload.get("changedAt"),
            payload.get("creationDate"),
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
        if isinstance(value, int | float):
            try:
                ts = float(value)
            except Exception:
                return None
            if ts <= 0:
                return None
            if ts > 10**12:
                ts = ts / 1000.0
            try:
                return datetime.fromtimestamp(ts, tz=UTC).replace(tzinfo=None)
            except Exception:
                return None
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

