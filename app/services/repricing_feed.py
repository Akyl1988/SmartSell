# app/services/repricing_feed.py
"""
Kaspi.kz integration: product feed generation, orders sync, and availability sync.

- Асинхронный httpx-клиент с таймаутами и повторными попытками (backoff).
- Безопасные мапперы статусов Kaspi -> наши статусы.
- Генерация XML-фида по активным товарам компании.
- Синхронизация заказов (загрузка, детализация, апдейт статусов).
- Апдейт доступности товара на Kaspi.
- Готово к расширению для авто-репрайсинга (читаем конфиг из Product.extra["repricing"]).

ВНИМАНИЕ: Этот модуль не выполняет миграции. Он использует уже существующие у нас поля моделей.
"""

from __future__ import annotations

import asyncio
import math
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import httpx
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import get_logger
from app.models import Order, OrderItem, Product

logger = get_logger(__name__)


def _utcnow() -> datetime:
    # uniform UTC (на выходе naive utc для согласованности с моделями)
    return datetime.utcnow()


def _ensure_str(v: Any) -> str:
    return "" if v is None else str(v)


def _xml_escape(text: str) -> str:
    # для значений вне CDATA
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


def _cdata(text: Optional[str]) -> str:
    if not text:
        return "<![CDATA[]]>"
    # без вложенных закрытий CDATA
    safe = text.replace("]]>", "]]&gt;")
    return f"<![CDATA[{safe}]]>"


class _RetryingAsyncClient:
    """
    Обёртка над httpx.AsyncClient с простым backoff-повтором на сетевые/5xx ошибки.
    """

    def __init__(self, *, timeout: float = 30.0, retries: int = 2, backoff_base: float = 0.5):
        self._client = httpx.AsyncClient(timeout=timeout)
        self._retries = max(0, retries)
        self._base = backoff_base

    async def request(self, method: str, url: str, **kwargs) -> httpx.Response:
        last_exc: Optional[Exception] = None
        for attempt in range(self._retries + 1):
            try:
                resp = await self._client.request(method, url, **kwargs)
                # Повторяем только на 5xx
                if resp.status_code >= 500:
                    raise httpx.HTTPStatusError("Server error", request=resp.request, response=resp)
                return resp
            except (httpx.TimeoutException, httpx.NetworkError, httpx.HTTPStatusError) as e:
                last_exc = e
                if attempt >= self._retries:
                    break
                sleep_for = self._base * (2 ** attempt)
                await asyncio.sleep(sleep_for)
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


class KaspiService:
    """
    Базовые операции интеграции с Kaspi.kz:
      - загрузка заказов/деталей
      - обновление статуса заказа
      - загрузка/апдейт товаров
      - генерация XML-фида
      - синхронизация заказов в локальную БД
    """

    def __init__(self, api_key: Optional[str] = None, base_url: Optional[str] = None):
        self.api_key = api_key or settings.KASPI_API_TOKEN or ""
        self.base_url = (base_url or settings.KASPI_API_URL or "").rstrip("/")
        self.headers = {
            "Authorization": f"Bearer {self.api_key}" if self.api_key else "",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

        # Валидация конфигурации — но не падаем сразу, чтобы можно было инстанцировать сервис в тестах.
        if not self.base_url:
            logger.warning("KaspiService: BASE URL не задан (settings.KASPI_API_URL).")
        if not self.api_key:
            logger.warning("KaspiService: API ключ не задан (settings.KASPI_API_TOKEN).")

    # ---------------------- HTTP helpers ----------------------

    def _client(self) -> _RetryingAsyncClient:
        # Единый таймаут и 2 ретрая на сетевые/5xx
        return _RetryingAsyncClient(timeout=30.0, retries=2, backoff_base=0.5)

    def _url(self, path: str) -> str:
        if not path.startswith("/"):
            path = "/" + path
        return f"{self.base_url}{path}"

    # ---------------------- Orders API ------------------------

    async def get_orders(
        self,
        *,
        date_from: Optional[datetime] = None,
        date_to: Optional[datetime] = None,
        status: Optional[str] = None,
        page: int = 1,
        page_size: int = 100,
    ) -> List[Dict[str, Any]]:
        """
        Получение заказов из Kaspi.
        Если даты не заданы — последние 24 часа.
        """
        if not date_from:
            date_from = _utcnow() - timedelta(days=1)
        if not date_to:
            date_to = _utcnow()

        params = {
            "dateFrom": date_from.strftime("%Y-%m-%dT%H:%M:%S"),
            "dateTo": date_to.strftime("%Y-%m-%dT%H:%M:%S"),
            "page": page,
            "pageSize": page_size,
        }
        if status:
            params["status"] = status

        async with self._client() as client:
            try:
                resp = await client.get(self._url("/orders"), headers=self.headers, params=params)
                resp.raise_for_status()
                data = resp.json() or {}
                orders = data.get("orders") or data.get("items") or []
                logger.info("Kaspi: получено %s заказов (page=%s).", len(orders), page)
                return orders
            except httpx.HTTPError as e:
                logger.error("Kaspi get_orders error: %s", e)
                raise RuntimeError(f"Failed to fetch orders from Kaspi: {e}") from e

    async def get_order_details(self, order_id: str) -> Optional[Dict[str, Any]]:
        async with self._client() as client:
            try:
                resp = await client.get(self._url(f"/orders/{order_id}"), headers=self.headers)
                resp.raise_for_status()
                return resp.json()
            except httpx.HTTPError as e:
                logger.error("Kaspi get_order_details(%s) error: %s", order_id, e)
                return None

    async def update_order_status(self, order_id: str, status: str) -> bool:
        payload = {"status": status}
        async with self._client() as client:
            try:
                resp = await client.patch(self._url(f"/orders/{order_id}/status"), headers=self.headers, json=payload)
                resp.raise_for_status()
                logger.info("Kaspi: статус заказа %s обновлён на '%s'.", order_id, status)
                return True
            except httpx.HTTPError as e:
                logger.error("Kaspi update_order_status(%s) error: %s", order_id, e)
                return False

    # ---------------------- Products API ----------------------

    async def get_products(self, *, page: int = 1, page_size: int = 100) -> List[Dict[str, Any]]:
        async with self._client() as client:
            try:
                resp = await client.get(self._url("/products"), headers=self.headers, params={"page": page, "pageSize": page_size})
                resp.raise_for_status()
                data = resp.json() or {}
                items = data.get("products") or data.get("items") or []
                logger.info("Kaspi: получено %s товаров (page=%s).", len(items), page)
                return items
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

    # ---------------------- Feed generation -------------------

    async def generate_product_feed(self, company_id: int, db: AsyncSession) -> str:
        """
        Генерация XML-фида по активным товарам.
        Включаем товары is_active=True, deleted_at IS NULL.
        """
        try:
            result = await db.execute(
                select(Product).where(
                    and_(Product.company_id == company_id, Product.is_active.is_(True), Product.deleted_at.is_(None))
                )
            )
            products: List[Product] = list(result.scalars().all())
            xml_content = self._generate_xml_feed(products)
            logger.info("Kaspi: сгенерирован фид из %s товаров.", len(products))
            return xml_content
        except Exception as e:
            logger.error("Kaspi generate_product_feed error: %s", e)
            raise RuntimeError(f"Failed to generate product feed: {e}") from e

    def _generate_xml_feed(self, products: List[Product]) -> str:
        """
        Минимальный валидный фид. Поля подогнаны под наши модели:
          - id: kaspi_product_id или наш id
          - sku, name, description
          - price: product.current_price
          - category: get_category_path()
          - brand: из extra.repricing.brand или пусто
          - availability: свободный остаток (free_stock) или 0 при предзаказе
          - image: image_url
        """
        lines: List[str] = ['<?xml version="1.0" encoding="UTF-8"?>', "<products>"]

        for p in products:
            pid = _ensure_str(p.kaspi_product_id or p.id)
            sku = _ensure_str(p.sku)
            name = _ensure_str(p.name)
            desc = _ensure_str(p.description or "")
            price = p.current_price if p.current_price is not None else None
            price_str = f"{price:.2f}" if price is not None else "0.00"
            category_path = _ensure_str(p.get_category_path())
            brand = self._extract_brand(p)
            availability = 0 if p.is_preorder() else max(0, int(p.free_stock))
            image = _ensure_str(p.image_url or "")

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
        Пытаемся взять бренд из Product.extra["brand"] или extra["repricing"]["brand"].
        Если нет — пустая строка (Kaspi допускает).
        """
        try:
            extra = p.get_extra()
            if "brand" in extra and extra["brand"]:
                return str(extra["brand"])
            rp = extra.get("repricing") or {}
            if isinstance(rp, dict) and rp.get("brand"):
                return str(rp["brand"])
        except Exception:
            pass
        return ""

    # ---------------------- Orders sync -----------------------

    async def sync_orders(self, company_id: int, db: AsyncSession) -> Dict[str, Any]:
        """
        Загружает свежие заказы из Kaspi, создаёт/обновляет у нас в БД.
        """
        created = 0
        updated = 0
        errors: List[str] = []

        try:
            kaspi_orders = await self.get_orders()
        except Exception as e:
            logger.error("Kaspi orders sync failed to fetch: %s", e)
            raise RuntimeError(f"Kaspi orders sync failed: {e}") from e

        for ko in kaspi_orders:
            try:
                order_id = _ensure_str(ko.get("id"))
                # Ищем существующий
                q = await db.execute(
                    select(Order).where(and_(Order.company_id == company_id, Order.external_id == order_id))
                )
                existing: Optional[Order] = q.scalar_one_or_none()

                mapped_status = self._map_kaspi_status(_ensure_str(ko.get("status")))
                if existing:
                    # Точечные обновления, чтобы не дёргать расчёты без смысла
                    if existing.status != mapped_status:
                        existing.status = mapped_status
                        updated += 1
                else:
                    # Создание нового
                    order = await self._create_order_from_kaspi(ko, company_id, db)
                    if order:
                        created += 1
            except Exception as e:
                logger.error("Error processing Kaspi order %s: %s", ko.get("id"), e)
                errors.append(str(e))

        try:
            await db.commit()
        except Exception as e:
            logger.error("DB commit error during Kaspi orders sync: %s", e)
            errors.append(f"commit: {e}")

        result = {
            "total_processed": len(kaspi_orders),
            "created": created,
            "updated": updated,
            "errors": errors,
        }
        logger.info("Kaspi orders sync completed: %s", result)
        return result

    async def _create_order_from_kaspi(self, kaspi_order: Dict[str, Any], company_id: int, db: AsyncSession) -> Optional[Order]:
        """
        Создаёт Order + OrderItem[] из данных Kaspi.
        Ожидаем, что модели Order/OrderItem реализуют calculate_total()/calculate_totals().
        """
        try:
            ext_id = _ensure_str(kaspi_order.get("id"))
            order_number = f"KASPI-{ext_id}"

            order = Order(
                company_id=company_id,
                order_number=order_number,
                external_id=ext_id,
                source="kaspi",
                status=self._map_kaspi_status(_ensure_str(kaspi_order.get("status"))),
                customer_phone=((kaspi_order.get("customer") or {}).get("phone")),
                customer_name=((kaspi_order.get("customer") or {}).get("name")),
                customer_address=kaspi_order.get("deliveryAddress"),
                delivery_method=kaspi_order.get("deliveryMode"),
                total_amount=kaspi_order.get("totalPrice", 0),
                currency="KZT",
            )
            db.add(order)
            await db.flush()  # чтобы получить order.id

            subtotal = 0
            for it in kaspi_order.get("items", []) or []:
                sku = _ensure_str(it.get("productSku"))
                name = _ensure_str(it.get("productName"))
                unit_price = it.get("basePrice", 0)
                qty = int(it.get("quantity", 1))
                # Пытаемся найти связанный продукт
                prod: Optional[Product] = None
                try:
                    res = await db.execute(
                        select(Product).where(
                            and_(
                                Product.company_id == company_id,
                                Product.kaspi_product_id == _ensure_str(it.get("productId")),
                            )
                        )
                    )
                    prod = res.scalar_one_or_none()
                except Exception:
                    prod = None

                oi = OrderItem(
                    order_id=order.id,
                    product_id=(prod.id if prod else None),
                    sku=sku,
                    name=name,
                    unit_price=unit_price,
                    quantity=qty,
                )
                oi.calculate_total()
                db.add(oi)
                subtotal += getattr(oi, "total_price", unit_price * qty)

            order.subtotal = subtotal
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

    # ---------------------- Availability sync -----------------

    async def sync_product_availability(self, product: Product) -> bool:
        """
        Апдейт доступности конкретного товара на стороне Kaspi.
        Используем p.kaspi_product_id; если его нет — пропускаем (возвращаем True, чтобы не валить пайплайн).
        """
        if not product.kaspi_product_id:
            logger.info("Kaspi availability: пропуск, у товара %s нет kaspi_product_id.", product.id)
            return True
        availability = 0 if product.is_preorder() else max(0, int(product.free_stock))
        return await self.update_product_availability(str(product.kaspi_product_id), availability)

    async def bulk_sync_availability(self, company_id: int, db: AsyncSession, *, limit: int = 500) -> Dict[str, int]:
        """
        Массовый апдейт доступности в Kaspi для активных товаров компании.
        """
        result = await db.execute(
            select(Product).where(
                and_(Product.company_id == company_id, Product.is_active.is_(True), Product.deleted_at.is_(None))
            )
        )
        products: List[Product] = list(result.scalars().all())
        ok = 0
        fail = 0

        # ограничиваем размер пачки (чтобы не DDOS'ить API)
        for p in products[: max(0, limit)]:
            try:
                if await self.sync_product_availability(p):
                    ok += 1
                else:
                    fail += 1
            except Exception as e:
                logger.error("Kaspi bulk availability error for product %s: %s", p.id, e)
                fail += 1

        stats = {"total": min(len(products), max(0, limit)), "ok": ok, "fail": fail}
        logger.info("Kaspi bulk availability sync completed: %s", stats)
        return stats
