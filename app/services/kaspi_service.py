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
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, Optional

import httpx
from sqlalchemy import and_, literal_column, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import get_logger
from app.models import Order, OrderItem, Product
from app.models.kaspi_order_sync_state import KaspiOrderSyncState
from app.models.order import OrderSource

logger = get_logger(__name__)


class KaspiSyncAlreadyRunning(RuntimeError):
    pass


# ---------------------- small utils ---------------------- #


def _utcnow() -> datetime:
    # Возвращаем naive UTC (совместимо с моделями)
    return datetime.utcnow()


def _as_str(v: Any) -> str:
    return "" if v is None else str(v)


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

    def __init__(self, *, timeout: float = 30.0, retries: int = 2, backoff_base: float = 0.5):
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

        if not self.base_url:
            logger.warning("KaspiService: BASE URL не задан (settings.KASPI_API_URL).")
        if not self.api_key:
            logger.warning("KaspiService: API ключ не задан (settings.KASPI_API_TOKEN).")

    # ---------------------- helpers ---------------------- #

    def _client(self) -> _RetryingAsyncClient:
        # Единые сетевые настройки
        return _RetryingAsyncClient(timeout=30.0, retries=2, backoff_base=0.5)

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
    ) -> list[dict[str, Any]]:
        """
        Получение заказов из Kaspi.
        Если даты не заданы — последние 24 часа.
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

        async with self._client() as client:
            try:
                resp = await client.get(self._url("/orders"), headers=self.headers, params=params)
                resp.raise_for_status()
                data = resp.json() or {}
                return data.get("orders") or data.get("items") or data.get("data") or []
            except httpx.HTTPError as e:
                logger.error("Kaspi get_orders error: %s", e)
                raise RuntimeError(f"Failed to fetch orders from Kaspi: {e}") from e

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

    # ---------------------- Orders sync (DB) ---------------------- #

    async def sync_orders(
        self,
        *,
        db: AsyncSession,
        company_id: int,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
        statuses: list[str] | None = None,
    ) -> dict[str, Any]:
        """Инкрементальная и идемпотентная синхронизация заказов Kaspi."""
        now = _utcnow()
        effective_to = date_to or now
        overlap = timedelta(minutes=2)
        fetched = 0
        inserted = 0
        updated = 0

        try:
            await db.rollback()
            async with db.begin():
                await db.execute(text("SET LOCAL lock_timeout = '2s'"))
                await db.execute(text("SET LOCAL statement_timeout = '10s'"))

                await self._acquire_company_lock(db, company_id)

                state = await self._load_or_create_state(db, company_id)

                prev_last_synced = state.last_synced_at
                prev_last_ext = state.last_external_order_id
                is_new_state = prev_last_synced is None and prev_last_ext is None

                base_from = date_from or prev_last_synced or (effective_to - timedelta(days=1))
                effective_from = base_from - overlap

                watermark = prev_last_synced or base_from
                last_ext = prev_last_ext
                made_progress = False

                for status in statuses or [None]:
                    page = 1
                    while True:
                        batch = await self.get_orders(
                            date_from=effective_from,
                            date_to=effective_to,
                            status=status,
                            page=page,
                            page_size=100,
                        )
                        if not batch:
                            break

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
                                payload.get("totalPrice") or payload.get("total_amount") or payload.get("total") or 0
                            )
                            updated_ts = self._extract_order_timestamp(payload) or effective_to

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
                                    delivery_method=payload.get("deliveryMode") or payload.get("delivery_mode") or None,
                                    total_amount=total_amount,
                                    currency=currency,
                                    updated_at=now,
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
                                        "updated_at": now,
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

                            await self._upsert_order_items(
                                db, order_id=order_pk, company_id=company_id, payload=payload
                            )

                        if len(batch) < 100:
                            break
                        page += 1

                if made_progress or is_new_state:
                    final_wm = watermark if prev_last_synced is None else max(prev_last_synced, watermark)
                    state.last_synced_at = final_wm
                    state.last_external_order_id = last_ext
                else:
                    final_wm = prev_last_synced or watermark
                    state.last_synced_at = prev_last_synced
                    state.last_external_order_id = prev_last_ext

                state.updated_at = now
        except KaspiSyncAlreadyRunning:
            raise
        except Exception:
            logger.exception("Kaspi orders sync internal error: company_id=%s", company_id)
            raise

        summary = {
            "company_id": company_id,
            "fetched": fetched,
            "inserted": inserted,
            "updated": updated,
            "from": effective_from.isoformat(),
            "to": effective_to.isoformat(),
            "watermark": (state.last_synced_at or final_wm).isoformat() if (state.last_synced_at or final_wm) else None,
        }

        logger.info(
            "Kaspi orders sync: company_id=%s fetched=%s inserted=%s updated=%s",
            company_id,
            fetched,
            inserted,
            updated,
        )
        return summary

    async def _upsert_order_items(
        self,
        db: AsyncSession,
        *,
        order_id: int,
        company_id: int,
        payload: dict[str, Any],
    ) -> None:
        items = payload.get("items") or payload.get("orderItems") or []
        if not items:
            return

        now = _utcnow()

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

    async def _acquire_company_lock(self, db: AsyncSession, company_id: int) -> None:
        lock_key = company_id * 97311
        res = await db.execute(text("SELECT pg_try_advisory_xact_lock(:lock_key)").bindparams(lock_key=lock_key))
        ok = res.scalar_one_or_none()
        if not ok:
            raise KaspiSyncAlreadyRunning("kaspi sync already running")

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
        except Exception:
            pass
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
