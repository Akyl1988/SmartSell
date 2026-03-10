from __future__ import annotations

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models import Product
from app.services.kaspi_service_utils import _as_str, _cdata, _xml_escape
from app.services.preorder_policy import evaluate_preorder_state

logger = get_logger("app.services.kaspi_service")


class KaspiServiceFeedMixin:
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

    async def sync_product_availability(self, product: Product, db: AsyncSession | None = None) -> bool:
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

        if db is not None and getattr(product, "company_id", None) is not None:
            await evaluate_preorder_state(db, company_id=product.company_id, product_id=product.id)

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
                if await self.sync_product_availability(p, db=db):
                    ok += 1
                else:
                    fail += 1
            except Exception as e:
                logger.error("Kaspi bulk availability error for product %s: %s", getattr(p, "id", "?"), e)
                fail += 1

        stats = {"total": len(products), "ok": ok, "fail": fail}
        logger.info("Kaspi bulk availability sync completed: %s", stats)
        return stats
