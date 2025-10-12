# app/services/repricing_service.py
from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Optional

from sqlalchemy.orm import Session

# Мягкие зависимости — в проекте они уже есть
try:
    from app.models.product import Product  # type: ignore
except Exception:  # pragma: no cover
    Product = object  # заглушка для type checker

try:
    from app.models.inventory_outbox import InventoryOutbox  # событие о смене цены
except Exception:  # pragma: no cover
    InventoryOutbox = None  # type: ignore

try:
    # наш интеграционный слой по Kaspi (в проекте присутствует)
    from app.services.kaspi_service import KaspiService  # type: ignore
except Exception:  # pragma: no cover
    KaspiService = object  # заглушка для type checker


log = logging.getLogger(__name__)


# =========================
# Конфигурация демпинга
# =========================
@dataclass
class RepricingConfig:
    enabled: bool = False  # включено/выключено для товара
    min_price: Optional[Decimal] = None  # нижняя граница, нельзя ниже
    max_price: Optional[Decimal] = None  # верхняя граница, нельзя выше
    step: Decimal = Decimal("1")  # шаг изменения (1/10/100 тг)
    undercut: Decimal = Decimal("1")  # на сколько быть ниже конкурента
    cooldown_seconds: int = 900  # кулдаун между изменениями (15 мин)
    ignore_seller_ids: list[str] = field(default_factory=list)  # дружественные магазины (ID)
    include_only_seller_ids: list[str] = field(
        default_factory=list
    )  # если указан список — сравниваем только с ними
    marketplace: str = "kaspi"  # пока каспи
    # Опционально — защита маржи (если есть себестоимость)
    min_margin_percent: Optional[Decimal] = None

    @classmethod
    def from_product(cls, p: Any) -> RepricingConfig:
        """
        Грузим конфиг из товара, если там есть JSON-поле (например p.settings / p.meta / p.extra).
        Без жёсткой привязки к схеме — читаем максимально безопасно.
        """
        data: dict[str, Any] = {}
        for field_name in (
            "repricing_config",
            "dempg_config",
            "pricing_rules",
            "extra",
            "settings",
        ):
            try:
                raw = getattr(p, field_name, None)
                if isinstance(raw, dict) and (
                    "repricing" in raw or "repricing_config" in raw or "demping" in raw
                ):
                    data = (
                        raw.get("repricing")
                        or raw.get("repricing_config")
                        or raw.get("demping")
                        or {}
                    )
                    break
                if isinstance(raw, dict) and any(
                    k in raw for k in ("enabled", "min_price", "max_price", "step", "undercut")
                ):
                    data = raw
                    break
            except Exception:
                continue

        def dec(x) -> Optional[Decimal]:
            if x is None or x == "":
                return None
            try:
                return Decimal(str(x))
            except Exception:
                return None

        return cls(
            enabled=bool(data.get("enabled", False)),
            min_price=dec(data.get("min_price")),
            max_price=dec(data.get("max_price")),
            step=dec(data.get("step")) or Decimal("1"),
            undercut=dec(data.get("undercut")) or Decimal("1"),
            cooldown_seconds=int(data.get("cooldown_seconds", 900) or 900),
            ignore_seller_ids=list(data.get("ignore_seller_ids", []))
            if isinstance(data.get("ignore_seller_ids"), list)
            else [],
            include_only_seller_ids=list(data.get("include_only_seller_ids", []))
            if isinstance(data.get("include_only_seller_ids"), list)
            else [],
            marketplace=str(data.get("marketplace", "kaspi")),
            min_margin_percent=dec(data.get("min_margin_percent")),
        )


# =========================
# Сервис демпинга
# =========================
class RepricingService:
    """
    Автоматическое управление ценой:
      - если конкурент ниже — опускаем на шаг (или на undercut), но не ниже min_price
      - если конкурент поднял цену/ушёл — поднимаем, но не выше max_price; если конкурентов нет — к max_price
      - игнорируем дружественные магазины
      - соблюдаем кулдаун и защиту от гонок
    """

    def __init__(self, session: Session, kaspi: Optional[KaspiService] = None):
        self.session = session
        self.kaspi = kaspi or getattr(
            __import__("app.services.kaspi_service", fromlist=["KaspiService"]),
            "KaspiService",
            None,
        )

    # ---------- Публичное API ----------
    def run_for_product_id(self, product_id: int) -> Optional[tuple[Decimal, str]]:
        product: Product = self._load_product(product_id)
        if not product:
            log.warning("Repricing: product %s not found", product_id)
            return None
        return self._reprice_product(product)

    def run_bulk(self, product_ids: Iterable[int]) -> dict[int, Optional[tuple[Decimal, str]]]:
        out: dict[int, Optional[tuple[Decimal, str]]] = {}
        for pid in product_ids:
            try:
                out[pid] = self.run_for_product_id(pid)
            except Exception as e:
                log.exception("Repricing bulk: failed for product %s: %s", pid, e)
                out[pid] = None
        return out

    # ---------- Основная логика ----------
    def _reprice_product(self, product: Product) -> Optional[tuple[Decimal, str]]:
        cfg = RepricingConfig.from_product(product)

        if not cfg.enabled:
            log.info("Repricing: product %s disabled", getattr(product, "id", None))
            return None

        # Кулдаун
        if not self._cooldown_ok(product, cfg.cooldown_seconds):
            log.debug("Repricing: cooldown active for product %s", getattr(product, "id", None))
            return None

        current_price = self._get_price(product)
        cost = self._get_cost(product)

        # Защита маржи
        if cfg.min_margin_percent is not None and cost is not None:
            min_allowed = (
                cost * (Decimal("1") + (cfg.min_margin_percent / Decimal("100")))
            ).quantize(Decimal("0.01"))
            if cfg.min_price is None or min_allowed > cfg.min_price:
                cfg.min_price = min_allowed

        competitor = self._best_competitor_price(product, cfg)
        new_price, reason = self._compute_new_price(current_price, competitor, cfg)

        if new_price is None or new_price == current_price:
            log.info(
                "Repricing: product %s price unchanged (%s)", getattr(product, "id", None), reason
            )
            return None

        # Применяем и сохраняем
        self._set_price(product, new_price)
        self._mark_repriced_at(product)

        try:
            self.session.flush()
        except Exception:
            # В некоторых местах цена может обновляться через сервис продуктов —
            # не мешаем внешней транзакции
            pass

        # Событие Outbox (если доступен)
        self._emit_outbox(product, old_price=current_price, new_price=new_price, reason=reason)

        log.info(
            "Repricing: product %s old=%s -> new=%s (%s)",
            getattr(product, "id", None),
            str(current_price),
            str(new_price),
            reason,
        )
        return new_price, reason

    # ---------- Компьют логики ----------
    @staticmethod
    def _compute_new_price(
        current: Decimal,
        competitor: Optional[tuple[Decimal, str]],
        cfg: RepricingConfig,
    ) -> tuple[Optional[Decimal], str]:
        """
        Возвращает (новая_цена, причина).
        competitor: (price, seller_id) или None если конкурентов нет.
        """
        step = cfg.step if cfg.step > 0 else Decimal("1")
        under = cfg.undercut if cfg.undercut >= 0 else Decimal("0")

        # Нет конкурентов -> поднимаемся к максимуму (если он задан)
        if competitor is None:
            if cfg.max_price is None:
                return None, "no_competitors_no_ceiling"
            target = cfg.max_price
            new_price = RepricingService._nudge_towards(current, target, step)
            new_price = RepricingService._clamp(new_price, cfg.min_price, cfg.max_price)
            if new_price == current:
                return None, "already_at_ceiling"
            return new_price, "to_ceiling_no_competitors"

        comp_price, comp_seller = competitor

        # Если конкурент ниже — спускаемся на undercut, но дискретно step
        if comp_price < current:
            target = (comp_price - under).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            new_price = RepricingService._nudge_towards(current, target, step, down=True)
            new_price = RepricingService._clamp(new_price, cfg.min_price, cfg.max_price)
            if new_price >= current:
                return None, "floor_reached"
            return new_price, f"down_vs_{comp_seller}"

        # Если конкурент выше — поднимаемся (но не выше max)
        if comp_price > current:
            if cfg.max_price is None:
                return None, "no_ceiling_set"
            target = min(
                cfg.max_price, comp_price
            )  # можно подниматься до конкурента, но не выше потолка
            new_price = RepricingService._nudge_towards(current, target, step)
            new_price = RepricingService._clamp(new_price, cfg.min_price, cfg.max_price)
            if new_price <= current:
                return None, "ceiling_reached"
            return new_price, f"up_vs_{comp_seller}"

        # Равно конкуренту
        if under > 0 and (cfg.min_price is None or (current - under) >= cfg.min_price):
            # можно дать символический undercut
            new_price = (current - under).quantize(Decimal("0.01"))
            new_price = RepricingService._clamp(new_price, cfg.min_price, cfg.max_price)
            if new_price < current:
                # приводим к сетке step
                new_price = RepricingService._nudge_towards(current, new_price, step, down=True)
                return new_price, f"equal_undercut_{under}"
        return None, "equal_no_change"

    @staticmethod
    def _nudge_towards(
        current: Decimal, target: Decimal, step: Decimal, down: bool | None = None
    ) -> Decimal:
        """
        Смещение от current к target дискретно шагом step.
        Если down=True — двигаемся только вниз. Если False — только вверх. Если None — по направлению к target.
        """
        if step <= 0:
            step = Decimal("1")

        current = Decimal(current)
        target = Decimal(target)

        if down is None:
            down = target < current

        if down:
            if target >= current:
                return current
            diff = current - target
            steps = max(1, int((diff / step).to_integral_value(rounding=ROUND_HALF_UP)))
            return (current - steps * step).quantize(Decimal("0.01"))
        else:
            if target <= current:
                return current
            diff = target - current
            steps = max(1, int((diff / step).to_integral_value(rounding=ROUND_HALF_UP)))
            return (current + steps * step).quantize(Decimal("0.01"))

    @staticmethod
    def _clamp(value: Decimal, floor: Optional[Decimal], ceil: Optional[Decimal]) -> Decimal:
        v = Decimal(value)
        if floor is not None and v < floor:
            v = Decimal(floor)
        if ceil is not None and v > ceil:
            v = Decimal(ceil)
        return v.quantize(Decimal("0.01"))

    # ---------- Данные и интеграции ----------
    def _best_competitor_price(
        self, product: Product, cfg: RepricingConfig
    ) -> Optional[tuple[Decimal, str]]:
        """
        Получаем цены конкурентов из Kaspi и выбираем «лучшую»:
          - минимальная цена среди релевантных продавцов
          - игнорируем дружественные (ignore_seller_ids)
          - если include_only_seller_ids не пуст — берём только их
        Ожидаемый формат из KaspiService: List[{"seller_id": "123", "price": "19990.00"}]
        """
        try:
            sku = getattr(product, "sku", None) or getattr(product, "code", None)
            product_id = getattr(product, "id", None)
            if not self.kaspi or not hasattr(self.kaspi, "get_competitor_prices"):
                log.debug("Repricing: KaspiService not available, skip competitor scan")
                return None

            rows: list[dict[str, Any]] = (
                self.kaspi.get_competitor_prices(  # type: ignore[attr-defined]
                    sku=sku, product_id=product_id
                )
                or []
            )

            filtered: list[tuple[Decimal, str]] = []
            for r in rows:
                sid = str(r.get("seller_id"))
                if sid in set(cfg.ignore_seller_ids):
                    continue
                if cfg.include_only_seller_ids and sid not in set(cfg.include_only_seller_ids):
                    continue
                price = Decimal(str(r.get("price")))
                filtered.append((price, sid))

            if not filtered:
                return None
            filtered.sort(key=lambda x: x[0])  # по цене
            return filtered[0]

        except Exception as e:
            log.warning(
                "Repricing: competitor scan failed for product %s: %s",
                getattr(product, "id", None),
                e,
            )
            return None

    # ---------- Работа с ценой товара ----------
    @staticmethod
    def _get_price(product: Product) -> Decimal:
        for field in ("price", "sale_price", "base_price"):
            val = getattr(product, field, None)
            if val is not None:
                try:
                    return Decimal(str(val))
                except Exception:
                    continue
        # Если нет явного поля — храните в extra/meta
        try:
            extra = getattr(product, "extra", None) or {}
            return Decimal(str(extra.get("price")))
        except Exception:
            raise ValueError("Product has no price field")

    @staticmethod
    def _get_cost(product: Product) -> Optional[Decimal]:
        for field in ("cost_price", "purchase_price", "cogs"):
            val = getattr(product, field, None)
            if val is not None:
                try:
                    return Decimal(str(val))
                except Exception:
                    continue
        try:
            extra = getattr(product, "extra", None) or {}
            v = extra.get("cost_price")
            return Decimal(str(v)) if v is not None else None
        except Exception:
            return None

    @staticmethod
    def _set_price(product: Product, new_price: Decimal) -> None:
        for field in ("price", "sale_price", "base_price"):
            if hasattr(product, field):
                setattr(product, field, Decimal(new_price))
                return
        # если нет стандартных полей — положим в extra
        extra = getattr(product, "extra", None)
        if isinstance(extra, dict):
            extra["price"] = str(Decimal(new_price))
            setattr(product, "extra", extra)
        else:
            setattr(product, "extra", {"price": str(Decimal(new_price))})

    @staticmethod
    def _mark_repriced_at(product: Product) -> None:
        # Пишем timestamp в одно из известных полей, если есть.
        for field in ("repriced_at", "price_updated_at", "updated_at"):
            if hasattr(product, field):
                try:
                    setattr(product, field, datetime.utcnow())
                    return
                except Exception:
                    continue

    def _load_product(self, product_id: int) -> Product:
        try:
            return self.session.get(Product, product_id)  # type: ignore
        except Exception:
            # На случай тестов без настоящей модели
            raise

    # ---------- Кулдаун ----------
    @staticmethod
    def _cooldown_ok(product: Product, cooldown_seconds: int) -> bool:
        if cooldown_seconds <= 0:
            return True
        # Опираемся на одно из полей updated_at/price_updated_at/repriced_at
        for field in ("repriced_at", "price_updated_at", "updated_at"):
            ts = getattr(product, field, None)
            if isinstance(ts, datetime):
                return (datetime.utcnow() - ts) >= timedelta(seconds=cooldown_seconds)
        return True

    # ---------- Outbox событие ----------
    def _emit_outbox(
        self, product: Product, *, old_price: Decimal, new_price: Decimal, reason: str
    ) -> None:
        if not InventoryOutbox:
            return
        payload = {
            "product_id": getattr(product, "id", None),
            "sku": getattr(product, "sku", None),
            "old_price": str(old_price),
            "new_price": str(new_price),
            "reason": reason,
            "updated_at": datetime.utcnow().isoformat(timespec="seconds"),
        }
        try:
            # используем safe_enqueue, если он реализован
            if hasattr(InventoryOutbox, "safe_enqueue"):
                InventoryOutbox.safe_enqueue(
                    self.session,
                    aggregate_type="product",
                    aggregate_id=getattr(product, "id", None) or "unknown",
                    event_type="price.repriced",
                    payload=payload,
                    channel="marketplace",
                    status="pending",
                )
            else:
                ev = InventoryOutbox(
                    aggregate_type="product",
                    aggregate_id=str(getattr(product, "id", None) or "unknown"),
                    event_type="price.repriced",
                    payload=payload,
                    channel="marketplace",
                    status="pending",
                )
                self.session.add(ev)
        except Exception as e:
            # никогда не ломаем основной флоу из-за Outbox
            log.warning("Repricing: outbox emit skipped: %s", e)
