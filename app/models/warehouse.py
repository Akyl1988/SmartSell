"""
Warehouse, ProductStock, and StockMovement models for inventory management.

Additions:
- BI/ML exports: stock movement trends (daily/weekly/monthly)
- Reorder automation by thresholds (alerts/tasks via Outbox)
- Extra movement reports (period dynamics)
- Soft-archive for warehouses and movements
- Cost & margin aggregators

Notes:
- Колонка working_hours тип JSON (кросс-СУБД). Для PostgreSQL создаём GIN-индекс
  через DDL only-if-postgres с CAST в JSONB и операторным классом jsonb_path_ops
  (SQLite не пострадает).
- Функция movements_timeseries использует generate_series/date_trunc и рассчитана на PostgreSQL,
  но имеет fallback-реализацию для SQLite (strftime) и generic SQL (грубая агрегация).
- Связи к внешним моделям однонаправленные (без обязательных back_populates у «другой стороны»).

Prod-grade нюансы:
- Избегаем падений при повторном импорте модуля: для таблиц задано __table_args__={"extend_existing": True}.
  Это не решает корень (двойной импорт), но защищает от InvalidRequestError в тестах/утилитах.
- GIN-индекс создаётся через DDL hook только на PostgreSQL и с IF NOT EXISTS.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from datetime import datetime, timedelta
from decimal import Decimal
from enum import Enum
from typing import Any, Optional

from sqlalchemy import DDL
from sqlalchemy import JSON as SAJSON  # кросс-СУБД JSON
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    and_,
    event,
    func,
)
from sqlalchemy import inspect as sa_inspect
from sqlalchemy import select, text
from sqlalchemy.exc import OperationalError, ProgrammingError
from sqlalchemy.orm import Session, relationship

from app.models.base import BaseModel
from app.models.inventory_outbox import InventoryOutbox

__all__ = [
    "MovementType",
    "Warehouse",
    "ProductStock",
    "StockMovement",
    "movements_analytics",
    "movements_timeseries",
    "low_stock_report",
    "scan_and_enqueue_reorder_alerts",
    "cogs_by_period",
    "margin_report",
]

log = logging.getLogger(__name__)


# ---- helpers ----
def _dialect_name(session: Session) -> str:
    try:
        bind = session.get_bind()
        return (bind.dialect.name if bind and bind.dialect else "") or ""
    except Exception:
        return ""


# ---- Адаптер: обеспечиваем InventoryOutbox.enqueue, если его нет ----
if not hasattr(InventoryOutbox, "enqueue"):

    def _enqueue(
        session: Session,
        *,
        aggregate_type: str,
        aggregate_id: int | str,
        event_type: str,
        payload: dict[str, Any] | None = None,
        channel: Optional[str] = None,
        status: str = "pending",
        next_attempt_in_seconds: Optional[int] = None,
    ):
        ev = InventoryOutbox(
            aggregate_type=str(aggregate_type),
            aggregate_id=str(aggregate_id),
            event_type=str(event_type),
            payload=payload or {},
            channel=channel,
            status=status,
        )
        if next_attempt_in_seconds:
            try:
                ev.next_attempt_at = datetime.utcnow() + timedelta(
                    seconds=int(next_attempt_in_seconds)
                )
            except Exception:
                pass
        return ev

    InventoryOutbox.enqueue = staticmethod(_enqueue)  # type: ignore[attr-defined]


# ---- Безопасная постановка в Outbox ----
def _outbox_enqueue_safe(
    session: Session,
    *,
    aggregate_type: str,
    aggregate_id: int | str,
    event_type: str,
    payload: dict[str, Any] | None = None,
    channel: Optional[str] = "erp",
    status: str = "pending",
    next_attempt_in_seconds: Optional[int] = None,
    log_prefix: str = "InventoryOutbox",
) -> Optional[InventoryOutbox]:
    try:
        if hasattr(InventoryOutbox, "safe_enqueue"):
            return InventoryOutbox.safe_enqueue(
                session,
                aggregate_type=aggregate_type,
                aggregate_id=aggregate_id,
                event_type=event_type,
                payload=payload,
                channel=channel,
                status=status,  # type: ignore[arg-type]
                next_attempt_in_seconds=next_attempt_in_seconds,
                log_prefix=log_prefix,
            )

        bind = session.get_bind()
        if not bind:
            log.info("%s: no bind — skipping enqueue.", log_prefix)
            return None

        insp = sa_inspect(bind)
        if not insp.has_table("inventory_outbox"):
            log.info("%s: table not present in DB — skipping enqueue.", log_prefix)
            return None

        log.info("%s: inventory_outbox present; consider implementing safe_enqueue().", log_prefix)
        return None

    except (OperationalError, ProgrammingError) as db_err:
        log.warning("%s: failed to enqueue (skipped): %s", log_prefix, db_err)
        return None
    except Exception as err:
        log.warning("%s: unexpected error during enqueue (skipped): %s", log_prefix, err)
        return None


# =========================
# Enums
# =========================
class MovementType(str, Enum):
    IN = "in"
    OUT = "out"
    TRANSFER_IN = "transfer_in"
    TRANSFER_OUT = "transfer_out"
    ADJUSTMENT = "adjustment"
    RESERVE = "reserve"
    RELEASE = "release"
    FULFILL = "fulfill"


# =========================
# Warehouse
# =========================
class Warehouse(BaseModel):
    """Warehouse model"""

    __tablename__ = "warehouses"

    id = Column(Integer, primary_key=True, index=True)
    company_id = Column(ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True)

    name = Column(String(255), nullable=False)
    code = Column(String(32), nullable=True, index=True)  # Internal warehouse code
    address = Column(Text, nullable=True)
    city = Column(String(100), nullable=True)
    region = Column(String(100), nullable=True)
    postal_code = Column(String(20), nullable=True)
    phone = Column(String(32), nullable=True)
    email = Column(String(255), nullable=True)
    manager_name = Column(String(255), nullable=True)
    is_active = Column(Boolean, default=True, nullable=False, index=True)
    is_main = Column(Boolean, default=False, nullable=False)

    # Кросс-СУБД JSON (в PostgreSQL индекс будет кастовать к JSONB)
    working_hours = Column(SAJSON, nullable=True)

    # Soft-archive
    is_archived = Column(Boolean, default=False, nullable=False, index=True)
    archived_at = Column(DateTime, nullable=True)

    # Однонаправленные связи
    company = relationship("Company")
    stocks = relationship("ProductStock", back_populates="warehouse", cascade="all, delete-orphan")

    __table_args__ = (
        UniqueConstraint("company_id", "code", name="uq_warehouse_company_code"),
        Index("ix_warehouses_company_active", "company_id", "is_active"),
        # Предохранитель от повторного объявления таблицы при двойной загрузке модуля:
        {"extend_existing": True},
    )

    def __repr__(self):
        return f"<Warehouse(id={self.id}, name='{self.name}')>"

    # --- Commands ---
    def activate(self):
        self.is_active = True

    def deactivate(self):
        self.is_active = False

    def set_main(self):
        self.is_main = True

    def unset_main(self):
        self.is_main = False

    def archive(self):
        self.is_archived = True
        self.archived_at = datetime.utcnow()

    def restore(self):
        self.is_archived = False
        self.archived_at = None

    # --- Queries/Utils ---
    def to_dict(self):
        return {col.name: getattr(self, col.name) for col in self.__table__.columns}

    def to_public_dict(self):
        return {
            "id": self.id,
            "company_id": self.company_id,
            "name": self.name,
            "code": self.code,
            "is_active": self.is_active,
            "is_main": self.is_main,
            "is_archived": self.is_archived,
            "city": self.city,
            "region": self.region,
            "working_hours": self.working_hours,
        }

    def get_active_stocks(self):
        return [stock for stock in self.stocks if stock.quantity > 0]

    def get_low_stocks(self):
        return [stock for stock in self.stocks if stock.is_low_stock]

    def restock_suggestions(self) -> list[dict[str, Any]]:
        out = []
        for s in self.stocks:
            if s.is_low_stock:
                target = (s.max_quantity or (s.min_quantity * 2)) or 0
                need = max(0, target - s.quantity)
                out.append(
                    {
                        "product_id": s.product_id,
                        "warehouse_id": s.warehouse_id,
                        "current_qty": int(s.quantity),
                        "reserved_qty": int(s.reserved_quantity),
                        "min_qty": int(s.min_quantity),
                        "max_qty": s.max_quantity,
                        "suggested_purchase_qty": int(need),
                    }
                )
        return out


# =========================
# ProductStock
# =========================
class ProductStock(BaseModel):
    """Product stock in warehouse"""

    __tablename__ = "product_stocks"

    id = Column(Integer, primary_key=True, index=True)
    product_id = Column(ForeignKey("products.id", ondelete="CASCADE"), nullable=False, index=True)
    warehouse_id = Column(
        ForeignKey("warehouses.id", ondelete="CASCADE"), nullable=False, index=True
    )
    quantity = Column(Integer, default=0, nullable=False)
    reserved_quantity = Column(Integer, default=0, nullable=False)
    min_quantity = Column(Integer, default=0, nullable=False)
    max_quantity = Column(Integer, nullable=True)
    location = Column(String(100), nullable=True)
    cost_price = Column(Numeric(14, 2), nullable=True)
    last_restocked_at = Column(DateTime, nullable=True)

    # Soft-archive
    is_archived = Column(Boolean, default=False, nullable=False, index=True)
    archived_at = Column(DateTime, nullable=True)

    # Relationships
    product = relationship("Product")
    warehouse = relationship("Warehouse", back_populates="stocks")
    movements = relationship("StockMovement", back_populates="stock", cascade="all, delete-orphan")

    __table_args__ = (
        UniqueConstraint("product_id", "warehouse_id", name="uq_product_warehouse"),
        CheckConstraint("quantity >= 0", name="ck_stock_quantity_nonneg"),
        CheckConstraint("reserved_quantity >= 0", name="ck_stock_reserved_nonneg"),
        CheckConstraint("reserved_quantity <= quantity", name="ck_stock_reserved_le_quantity"),
        CheckConstraint(
            "(max_quantity IS NULL) OR (max_quantity >= 0)", name="ck_stock_max_nonneg"
        ),
        CheckConstraint("min_quantity >= 0", name="ck_stock_min_nonneg"),
        CheckConstraint(
            "(max_quantity IS NULL) OR (min_quantity <= max_quantity)", name="ck_stock_min_le_max"
        ),
        Index("ix_stock_product_warehouse_qty", "product_id", "warehouse_id", "quantity"),
        Index("ix_stock_low", "warehouse_id", "min_quantity"),
        {"extend_existing": True},
    )

    # ---------- Properties ----------
    @property
    def available_quantity(self) -> int:
        return max(0, int(self.quantity) - int(self.reserved_quantity))

    @property
    def is_low_stock(self) -> bool:
        return int(self.quantity) <= int(self.min_quantity)

    # ---------- Commands ----------
    def archive(self):
        self.is_archived = True
        self.archived_at = datetime.utcnow()

    def restore(self):
        self.is_archived = False
        self.archived_at = None

    def reserve(self, quantity: int) -> bool:
        qty = int(quantity or 0)
        if qty <= 0:
            return False
        if self.available_quantity >= qty:
            self.reserved_quantity += qty
            return True
        return False

    def release_reservation(self, quantity: int):
        self.reserved_quantity = max(0, int(self.reserved_quantity) - max(0, int(quantity or 0)))

    def fulfill_reservation(self, quantity: int):
        qty = max(0, int(quantity or 0))
        if qty == 0:
            return
        fulfill_qty = min(qty, int(self.reserved_quantity))
        self.reserved_quantity = int(self.reserved_quantity) - fulfill_qty
        self.quantity = max(0, int(self.quantity) - fulfill_qty)

    def add_stock(self, qty: int, cost_price: Optional[Decimal] = None):
        """Служебный метод (оставлен для обратной совместимости). Не использовать в receive()."""
        q = max(0, int(qty or 0))
        if q == 0:
            return
        old_qty = int(self.quantity)
        self.quantity = old_qty + q
        self.last_restocked_at = datetime.utcnow()
        if cost_price is not None:
            self._recalc_avg_cost(old_qty=old_qty, add_qty=q, add_cost=Decimal(cost_price))

    def remove_stock(self, qty: int):
        q = max(0, int(qty or 0))
        if q == 0:
            return
        self.quantity = max(0, int(self.quantity) - q)

    def set_location(self, location: str):
        self.location = location

    # ---------- Costing ----------
    def _recalc_avg_cost(self, *, old_qty: int, add_qty: int, add_cost: Decimal):
        if add_qty <= 0:
            return
        old_cost = Decimal(self.cost_price or 0)
        numerator = (Decimal(old_qty) * old_cost) + (Decimal(add_qty) * Decimal(add_cost))
        denom = Decimal(old_qty + add_qty)
        self.cost_price = (numerator / denom).quantize(Decimal("0.01"))

    # ---------- Movement helpers ----------
    def _write_movement(
        self,
        session: Session,
        *,
        movement_type: str,
        quantity: int,
        user_id: Optional[int] = None,
        reference_type: Optional[str] = None,
        reference_id: Optional[int] = None,
        reason: Optional[str] = None,
        notes: Optional[str] = None,
        order_id: Optional[int] = None,
        product_id: Optional[int] = None,
        commit: bool = False,
        erp_hook: bool = True,
        cost_price_for_avg: Optional[Decimal] = None,
    ):
        prev_qty = int(self.quantity)
        delta = int(quantity)
        new_qty = prev_qty + delta
        if new_qty < 0:
            raise ValueError("Resulting stock quantity cannot be negative")

        # Только для прихода
        if movement_type == MovementType.IN.value and delta > 0:
            if cost_price_for_avg is not None:
                self._recalc_avg_cost(
                    old_qty=prev_qty, add_qty=delta, add_cost=Decimal(cost_price_for_avg)
                )
            self.last_restocked_at = datetime.utcnow()

        self.quantity = new_qty

        m = StockMovement(
            stock_id=self.id,
            product_id=product_id if product_id is not None else self.product_id,
            movement_type=movement_type,
            quantity=delta,
            previous_quantity=prev_qty,
            new_quantity=new_qty,
            user_id=user_id,
            reference_type=reference_type,
            reference_id=reference_id,
            reason=reason,
            notes=notes,
            order_id=order_id,
        )
        session.add(m)
        session.flush()

        if erp_hook:
            _outbox_enqueue_safe(
                session,
                aggregate_type="stock_movement",
                aggregate_id=m.id,
                event_type="stock.changed",
                payload=m.to_public_dict(),
                channel="erp",
                log_prefix="stock._write_movement",
            )

        if commit:
            session.commit()
        return m

    def receive(
        self,
        session: Session,
        qty: int,
        *,
        user_id: Optional[int] = None,
        cost_price: Optional[Decimal] = None,
        reference_type: Optional[str] = None,
        reference_id: Optional[int] = None,
        notes: Optional[str] = None,
        commit: bool = False,
    ):
        return self._write_movement(
            session,
            movement_type=MovementType.IN.value,
            quantity=qty,
            user_id=user_id,
            reference_type=reference_type,
            reference_id=reference_id,
            reason="receive",
            notes=notes,
            commit=commit,
            cost_price_for_avg=cost_price,
        )

    def ship(
        self,
        session: Session,
        qty: int,
        *,
        user_id: Optional[int] = None,
        reference_type: Optional[str] = None,
        reference_id: Optional[int] = None,
        notes: Optional[str] = None,
        commit: bool = False,
    ):
        return self._write_movement(
            session,
            movement_type=MovementType.OUT.value,
            quantity=-abs(int(qty or 0)),
            user_id=user_id,
            reference_type=reference_type,
            reference_id=reference_id,
            reason="ship",
            notes=notes,
            commit=commit,
        )

    def reserve_and_log(
        self,
        session: Session,
        qty: int,
        *,
        user_id: Optional[int] = None,
        reference_type: Optional[str] = None,
        reference_id: Optional[int] = None,
        notes: Optional[str] = None,
        commit: bool = False,
    ):
        if not self.reserve(qty):
            raise ValueError("Not enough stock to reserve")
        m = StockMovement(
            stock_id=self.id,
            product_id=self.product_id,
            movement_type=MovementType.RESERVE.value,
            quantity=int(qty),
            previous_quantity=int(self.quantity),
            new_quantity=int(self.quantity),
            user_id=user_id,
            reference_type=reference_type,
            reference_id=reference_id,
            reason="reserve",
            notes=notes,
        )
        session.add(m)
        session.flush()
        _outbox_enqueue_safe(
            session,
            aggregate_type="stock_movement",
            aggregate_id=m.id,
            event_type="stock.reserved",
            payload=m.to_public_dict(),
            channel="erp",
            log_prefix="stock.reserve_and_log",
        )
        if commit:
            session.commit()
        return m

    def release_and_log(
        self,
        session: Session,
        qty: int,
        *,
        user_id: Optional[int] = None,
        reference_type: Optional[str] = None,
        reference_id: Optional[int] = None,
        notes: Optional[str] = None,
        commit: bool = False,
    ):
        before = int(self.reserved_quantity)
        self.release_reservation(qty)
        delta = before - int(self.reserved_quantity)
        m = StockMovement(
            stock_id=self.id,
            product_id=self.product_id,
            movement_type=MovementType.RELEASE.value,
            quantity=int(delta),
            previous_quantity=int(self.quantity),
            new_quantity=int(self.quantity),
            user_id=user_id,
            reference_type=reference_type,
            reference_id=reference_id,
            reason="release",
            notes=notes,
        )
        session.add(m)
        session.flush()
        _outbox_enqueue_safe(
            session,
            aggregate_type="stock_movement",
            aggregate_id=m.id,
            event_type="stock.release",
            payload=m.to_public_dict(),
            channel="erp",
            log_prefix="stock.release_and_log",
        )
        if commit:
            session.commit()
        return m

    def fulfill_and_log(
        self,
        session: Session,
        qty: int,
        *,
        user_id: Optional[int] = None,
        reference_type: Optional[str] = None,
        reference_id: Optional[int] = None,
        notes: Optional[str] = None,
        commit: bool = False,
    ):
        before_qty = int(self.quantity)
        self.fulfill_reservation(qty)
        m = StockMovement(
            stock_id=self.id,
            product_id=self.product_id,
            movement_type=MovementType.FULFILL.value,
            quantity=-abs(int(qty or 0)),
            previous_quantity=before_qty,
            new_quantity=int(self.quantity),
            user_id=user_id,
            reference_type=reference_type,
            reference_id=reference_id,
            reason="fulfill",
            notes=notes,
        )
        session.add(m)
        session.flush()
        _outbox_enqueue_safe(
            session,
            aggregate_type="stock_movement",
            aggregate_id=m.id,
            event_type="stock.fulfilled",
            payload=m.to_public_dict(),
            channel="erp",
            log_prefix="stock.fulfill_and_log",
        )
        if commit:
            session.commit()
        return m

    @staticmethod
    def transfer(
        session: Session,
        *,
        product_id: int,
        from_warehouse_id: int,
        to_warehouse_id: int,
        qty: int,
        user_id: Optional[int] = None,
        reference_type: Optional[str] = "transfer",
        reference_id: Optional[int] = None,
        notes: Optional[str] = None,
        commit: bool = False,
    ) -> tuple[StockMovement, StockMovement]:
        if qty <= 0:
            raise ValueError("Transfer qty must be positive")

        from_stock = session.execute(
            select(ProductStock).where(
                ProductStock.product_id == product_id,
                ProductStock.warehouse_id == from_warehouse_id,
            )
        ).scalar_one_or_none()
        to_stock = session.execute(
            select(ProductStock).where(
                ProductStock.product_id == product_id,
                ProductStock.warehouse_id == to_warehouse_id,
            )
        ).scalar_one_or_none()

        if not from_stock or not to_stock:
            raise ValueError("Both source and destination stocks must exist")

        m_out = from_stock._write_movement(
            session,
            movement_type=MovementType.TRANSFER_OUT.value,
            quantity=-abs(int(qty)),
            user_id=user_id,
            reference_type=reference_type,
            reference_id=reference_id,
            reason="transfer_out",
            notes=notes,
            commit=False,
        )
        m_in = to_stock._write_movement(
            session,
            movement_type=MovementType.TRANSFER_IN.value,
            quantity=abs(int(qty)),
            user_id=user_id,
            reference_type=reference_type,
            reference_id=reference_id,
            reason="transfer_in",
            notes=notes,
            commit=False,
        )

        _outbox_enqueue_safe(
            session,
            aggregate_type="product_stock",
            aggregate_id=to_stock.id,
            event_type="stock.transferred",
            payload={
                "product_id": product_id,
                "from_warehouse_id": from_warehouse_id,
                "to_warehouse_id": to_warehouse_id,
                "qty": int(qty),
                "movement_out_id": m_out.id,
                "movement_in_id": m_in.id,
            },
            channel="erp",
            log_prefix="stock.transfer",
        )
        if commit:
            session.commit()
        return m_out, m_in

    # ---------- Queries/Exports ----------
    def to_dict(self):
        return {col.name: getattr(self, col.name) for col in self.__table__.columns}

    def to_public_dict(self):
        return {
            "id": self.id,
            "product_id": self.product_id,
            "warehouse_id": self.warehouse_id,
            "quantity": int(self.quantity),
            "reserved_quantity": int(self.reserved_quantity),
            "available_quantity": int(self.available_quantity),
            "min_quantity": int(self.min_quantity),
            "max_quantity": self.max_quantity,
            "location": self.location,
            "cost_price": str(self.cost_price) if self.cost_price is not None else None,
            "last_restocked_at": self.last_restocked_at.isoformat(timespec="seconds")
            if self.last_restocked_at
            else None,
            "is_archived": self.is_archived,
        }

    @staticmethod
    def export_inventory_snapshot(
        session: Session,
        *,
        company_id: Optional[int] = None,
        warehouse_ids: Optional[Iterable[int]] = None,
        product_ids: Optional[Iterable[int]] = None,
        only_low: bool = False,
        exclude_archived: bool = True,
    ) -> list[dict[str, Any]]:
        q = select(ProductStock)
        if warehouse_ids:
            q = q.where(ProductStock.warehouse_id.in_(list(warehouse_ids)))
        if product_ids:
            q = q.where(ProductStock.product_id.in_(list(product_ids)))
        if only_low:
            q = q.where(ProductStock.quantity <= ProductStock.min_quantity)
        if exclude_archived:
            q = q.where(ProductStock.is_archived.is_(False))
        if company_id is not None:
            q = q.join(Warehouse, Warehouse.id == ProductStock.warehouse_id).where(
                Warehouse.company_id == company_id
            )
        rows = session.execute(q).scalars().all()
        return [s.to_public_dict() for s in rows]


# =========================
# StockMovement
# =========================
class StockMovement(BaseModel):
    """Stock movement tracking"""

    __tablename__ = "stock_movements"

    id = Column(Integer, primary_key=True, index=True)
    # допускаем движения без stock_id (только product_id)
    stock_id = Column(
        ForeignKey("product_stocks.id", ondelete="CASCADE"), nullable=True, index=True
    )
    product_id = Column(ForeignKey("products.id", ondelete="CASCADE"), nullable=True, index=True)
    movement_type = Column(String(32), nullable=False, index=True)
    quantity = Column(Integer, nullable=False)  # Positive for in, negative for out
    previous_quantity = Column(Integer, nullable=False)
    new_quantity = Column(Integer, nullable=False)
    reference_type = Column(String(32), nullable=True)
    reference_id = Column(Integer, nullable=True, index=True)
    reason = Column(String(255), nullable=True)
    notes = Column(Text, nullable=True)
    user_id = Column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    order_id = Column(ForeignKey("orders.id", ondelete="CASCADE"), nullable=True, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    # Soft-archive
    is_archived = Column(Boolean, default=False, nullable=False, index=True)
    archived_at = Column(DateTime, nullable=True)

    # Relationships (однонаправленно)
    stock = relationship("ProductStock", back_populates="movements")
    user = relationship("User")
    order = relationship("Order")
    product = relationship("Product")

    __table_args__ = (
        CheckConstraint("new_quantity >= 0", name="ck_movement_newqty_nonneg"),
        CheckConstraint(
            "(stock_id IS NOT NULL) OR (product_id IS NOT NULL)",
            name="ck_stock_movement_ref_nonnull",
        ),
        Index("ix_movements_stock_created", "stock_id", "created_at"),
        Index("ix_movements_product_type", "product_id", "movement_type"),
        Index("ix_movements_order", "order_id"),
        {"extend_existing": True},
    )

    def __repr__(self):
        return f"<StockMovement(id={self.id}, type='{self.movement_type}', qty={self.quantity})>"

    def to_dict(self):
        return {col.name: getattr(self, col.name) for col in self.__table__.columns}

    def to_public_dict(self):
        return {
            "id": self.id,
            "stock_id": self.stock_id,
            "product_id": self.product_id,
            "movement_type": self.movement_type,
            "quantity": int(self.quantity),
            "previous_quantity": int(self.previous_quantity),
            "new_quantity": int(self.new_quantity),
            "reference_type": self.reference_type,
            "reference_id": self.reference_id,
            "reason": self.reason,
            "order_id": self.order_id,
            "created_at": self.created_at.isoformat(timespec="seconds")
            if self.created_at
            else None,
            "is_archived": self.is_archived,
        }

    def archive(self):
        self.is_archived = True
        self.archived_at = datetime.utcnow()

    def restore(self):
        self.is_archived = False
        self.archived_at = None

    @classmethod
    def log_movement(
        cls,
        session: Session,
        stock: ProductStock,
        movement_type: str,
        quantity: int,
        user_id: int = None,
        reference_type: str = None,
        reference_id: int = None,
        reason: str = None,
        notes: str = None,
        order_id: int = None,
        product_id: int = None,
        commit: bool = False,
    ):
        """Универсальный лог движений. По умолчанию без commit — безопасно для внешних транзакций."""
        prev_qty = int(stock.quantity)
        new_qty = prev_qty + int(quantity)
        if new_qty < 0:
            raise ValueError("Resulting stock quantity cannot be negative")
        stock.quantity = new_qty
        movement = cls(
            stock_id=stock.id,
            movement_type=movement_type,
            quantity=int(quantity),
            previous_quantity=prev_qty,
            new_quantity=new_qty,
            user_id=user_id,
            reference_type=reference_type,
            reference_id=reference_id,
            reason=reason,
            notes=notes,
            order_id=order_id,
            product_id=product_id if product_id else stock.product_id,
        )
        session.add(movement)
        session.flush()
        _outbox_enqueue_safe(
            session,
            aggregate_type="stock_movement",
            aggregate_id=movement.id,
            event_type="stock.changed",
            payload=movement.to_public_dict(),
            channel="erp",
            log_prefix="StockMovement.log_movement",
        )
        if commit:
            session.commit()
        return movement

    def get_related_order(self):
        return self.order

    def get_related_product(self):
        return self.product

    def get_related_stock(self):
        return self.stock


# =========================
# Analytics & BI/ML Exports
# =========================
def movements_analytics(
    session: Session,
    *,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    warehouse_id: Optional[int] = None,
    product_id: Optional[int] = None,
    movement_types: Optional[Iterable[str]] = None,
    exclude_archived: bool = True,
) -> list[dict[str, Any]]:
    """Агрегация движений по типам: count, sum(qty)."""
    q = select(
        StockMovement.movement_type,
        func.count(StockMovement.id).label("cnt"),
        func.coalesce(func.sum(StockMovement.quantity), 0).label("qty_sum"),
    ).join(ProductStock, ProductStock.id == StockMovement.stock_id)

    conds = []
    if exclude_archived:
        conds.append(StockMovement.is_archived.is_(False))
    if date_from:
        conds.append(StockMovement.created_at >= date_from)
    if date_to:
        conds.append(StockMovement.created_at < date_to)
    if warehouse_id is not None:
        conds.append(ProductStock.warehouse_id == warehouse_id)
    if product_id is not None:
        conds.append(ProductStock.product_id == product_id)
    if movement_types:
        conds.append(StockMovement.movement_type.in_(list(movement_types)))

    if conds:
        q = q.where(and_(*conds))

    q = q.group_by(StockMovement.movement_type).order_by(StockMovement.movement_type)
    rows = session.execute(q).all()
    return [
        {"movement_type": mtype, "count": int(cnt or 0), "quantity_sum": int(qty_sum or 0)}
        for (mtype, cnt, qty_sum) in rows
    ]


def movements_timeseries(
    session: Session,
    *,
    bucket: str = "day",  # "day" | "week" | "month"
    date_from: datetime,
    date_to: datetime,
    warehouse_id: Optional[int] = None,
    product_id: Optional[int] = None,
    exclude_archived: bool = True,
) -> list[dict[str, Any]]:
    """
    Динамика движений по периодам (сумма qty), для BI/ML.
    PostgreSQL: generate_series + date_trunc.
    SQLite: strftime fallback. Generic: date().
    """
    if not (isinstance(date_from, datetime) and isinstance(date_to, datetime)):
        raise ValueError("date_from and date_to must be datetime")
    if date_to <= date_from:
        raise ValueError("date_to must be greater than date_from")

    bucket_map = {"day": "day", "week": "week", "month": "month"}
    if bucket not in bucket_map:
        raise ValueError("bucket must be 'day', 'week' or 'month'")
    trunc = bucket_map[bucket]

    dialect = _dialect_name(session).lower()

    if dialect == "postgresql":
        conds = ["1=1"]
        params: dict[str, Any] = {"df": date_from, "dt": date_to}
        if exclude_archived:
            conds.append("sm.is_archived = false")
        if warehouse_id is not None:
            conds.append("ps.warehouse_id = :wid")
            params["wid"] = warehouse_id
        if product_id is not None:
            conds.append("ps.product_id = :pid")
            params["pid"] = product_id

        sql = text(
            f"""
            WITH series AS (
                SELECT generate_series(date_trunc('{trunc}', :df::timestamp),
                                       date_trunc('{trunc}', :dt::timestamp),
                                       '1 {trunc}') AS bucket_start
            ),
            mv AS (
                SELECT date_trunc('{trunc}', sm.created_at) AS bucket,
                       SUM(sm.quantity)::int AS qty_sum
                FROM stock_movements sm
                JOIN product_stocks ps ON ps.id = sm.stock_id
                WHERE sm.created_at >= :df AND sm.created_at < :dt
                  AND {' AND '.join(conds)}
                GROUP BY 1
            )
            SELECT s.bucket_start AS bucket,
                   COALESCE(mv.qty_sum, 0) AS qty_sum
            FROM series s
            LEFT JOIN mv ON mv.bucket = s.bucket_start
            ORDER BY s.bucket_start
        """
        )
        rows = session.execute(sql, params).all()
        return [
            {"bucket": r[0].isoformat(timespec="seconds"), "qty_sum": int(r[1] or 0)} for r in rows
        ]

    if dialect == "sqlite":
        if bucket == "day":
            bucket_expr = func.strftime("%Y-%m-%d 00:00:00", StockMovement.created_at)
        elif bucket == "week":
            bucket_expr = func.printf(
                "%s-W%02d",
                func.strftime("%Y", StockMovement.created_at),
                func.strftime("%W", StockMovement.created_at),
            )
        else:  # month
            bucket_expr = func.strftime("%Y-%m-01 00:00:00", StockMovement.created_at)

        q = (
            select(bucket_expr.label("bucket"), func.coalesce(func.sum(StockMovement.quantity), 0))
            .select_from(StockMovement)
            .join(ProductStock, ProductStock.id == StockMovement.stock_id)
            .where(StockMovement.created_at >= date_from, StockMovement.created_at < date_to)
        )
        if exclude_archived:
            q = q.where(StockMovement.is_archived.is_(False))
        if warehouse_id is not None:
            q = q.where(ProductStock.warehouse_id == warehouse_id)
        if product_id is not None:
            q = q.where(ProductStock.product_id == product_id)

        q = q.group_by("bucket").order_by("bucket")
        rows = session.execute(q).all()
        return [{"bucket": str(b), "qty_sum": int(qty or 0)} for b, qty in rows]

    # generic
    bucket_expr = func.date(StockMovement.created_at)
    q = (
        select(bucket_expr.label("bucket"), func.coalesce(func.sum(StockMovement.quantity), 0))
        .select_from(StockMovement)
        .join(ProductStock, ProductStock.id == StockMovement.stock_id)
        .where(StockMovement.created_at >= date_from, StockMovement.created_at < date_to)
    )
    if exclude_archived:
        q = q.where(StockMovement.is_archived.is_(False))
    if warehouse_id is not None:
        q = q.where(ProductStock.warehouse_id == warehouse_id)
    if product_id is not None:
        q = q.where(ProductStock.product_id == product_id)
    q = q.group_by("bucket").order_by("bucket")
    rows = session.execute(q).all()
    return [{"bucket": str(b), "qty_sum": int(qty or 0)} for b, qty in rows]


def low_stock_report(
    session: Session, *, warehouse_id: Optional[int] = None
) -> list[dict[str, Any]]:
    """Список низких остатков."""
    q = select(ProductStock).where(ProductStock.quantity <= ProductStock.min_quantity)
    if warehouse_id is not None:
        q = q.where(ProductStock.warehouse_id == warehouse_id)
    rows = session.execute(q).scalars().all()
    return [s.to_public_dict() for s in rows]


# =========================
# Reorder Automation
# =========================
def scan_and_enqueue_reorder_alerts(
    session: Session,
    *,
    company_id: Optional[int] = None,
    warehouse_ids: Optional[Iterable[int]] = None,
    only_when_low: bool = True,
    channel: str = "task",
) -> list[InventoryOutbox]:
    """
    Ищет позиции, требующие дозаказа, и ставит задачи/алерты в Outbox.
    Возвращает список созданных событий (или пустой, если Outbox недоступен).
    """
    q = select(ProductStock, Warehouse).join(Warehouse, Warehouse.id == ProductStock.warehouse_id)
    q = q.where(ProductStock.is_archived.is_(False), Warehouse.is_archived.is_(False))
    if company_id is not None:
        q = q.where(Warehouse.company_id == company_id)
    if warehouse_ids:
        q = q.where(ProductStock.warehouse_id.in_(list(warehouse_ids)))
    if only_when_low:
        q = q.where(ProductStock.quantity <= ProductStock.min_quantity)

    rows = session.execute(q).all()
    events: list[InventoryOutbox] = []
    for stock, wh in rows:
        target = (stock.max_quantity or (stock.min_quantity * 2)) or 0
        need = max(0, target - stock.quantity)
        payload = {
            "product_id": stock.product_id,
            "warehouse_id": stock.warehouse_id,
            "current_qty": int(stock.quantity),
            "reserved_qty": int(stock.reserved_quantity),
            "min_qty": int(stock.min_quantity),
            "max_qty": stock.max_quantity,
            "suggested_purchase_qty": int(need),
            "warehouse_name": wh.name,
        }
        ev = _outbox_enqueue_safe(
            session,
            aggregate_type="product_stock",
            aggregate_id=stock.id,
            event_type="reorder.alert",
            payload=payload,
            channel=channel,
            log_prefix="stock.reorder_scan",
        )
        if ev is not None:
            events.append(ev)
    return events


# =========================
# Cost & Margin aggregators
# =========================
def cogs_by_period(
    session: Session,
    *,
    date_from: datetime,
    date_to: datetime,
    warehouse_id: Optional[int] = None,
    product_id: Optional[int] = None,
    exclude_archived: bool = True,
) -> list[dict[str, Any]]:
    """
    COGS (себестоимость) на основании исходящих движений (OUT/FULFILL).
    Использует текущую cost_price в ProductStock как приближение.
    """
    out_types = (MovementType.OUT.value, MovementType.FULFILL.value)
    q = (
        select(
            ProductStock.product_id,
            ProductStock.warehouse_id,
            func.coalesce(func.sum(-StockMovement.quantity), 0).label("units"),
            func.avg(ProductStock.cost_price).label("avg_cost"),
        )
        .join(ProductStock, ProductStock.id == StockMovement.stock_id)
        .where(
            StockMovement.movement_type.in_(out_types),
            StockMovement.created_at >= date_from,
            StockMovement.created_at < date_to,
        )
    )
    if exclude_archived:
        q = q.where(StockMovement.is_archived.is_(False), ProductStock.is_archived.is_(False))
    if warehouse_id is not None:
        q = q.where(ProductStock.warehouse_id == warehouse_id)
    if product_id is not None:
        q = q.where(ProductStock.product_id == product_id)

    q = q.group_by(ProductStock.product_id, ProductStock.warehouse_id)
    rows = session.execute(q).all()
    result: list[dict[str, Any]] = []
    for pid, wid, units, avg_cost in rows:
        units = int(units or 0)
        avg_cost_dec = Decimal(avg_cost or 0).quantize(Decimal("0.01"))
        cost = (avg_cost_dec * Decimal(units)).quantize(Decimal("0.01"))
        result.append(
            {
                "product_id": pid,
                "warehouse_id": wid,
                "units_sold": units,
                "avg_cost": str(avg_cost_dec),
                "cogs": str(cost),
            }
        )
    return result


def margin_report(
    session: Session,
    *,
    date_from: datetime,
    date_to: datetime,
    price_fetcher: Optional[Callable[[int], Decimal]] = None,
    warehouse_id: Optional[int] = None,
    product_id: Optional[int] = None,
) -> list[dict[str, Any]]:
    """
    Отчёт по марже: revenue - COGS.
    Требует функцию price_fetcher(product_id)->Decimal (продажная цена за единицу).
    """
    cogs = cogs_by_period(
        session,
        date_from=date_from,
        date_to=date_to,
        warehouse_id=warehouse_id,
        product_id=product_id,
    )
    report: list[dict[str, Any]] = []
    for row in cogs:
        pid = row["product_id"]
        units = int(row["units_sold"])
        unit_price = Decimal("0.00")
        if price_fetcher:
            try:
                unit_price = Decimal(price_fetcher(pid) or 0)
            except Exception:
                unit_price = Decimal("0.00")
        revenue = (unit_price * Decimal(units)).quantize(Decimal("0.01"))
        margin = (revenue - Decimal(row["cogs"])).quantize(Decimal("0.01"))
        report.append(
            {
                **row,
                "unit_price": str(unit_price.quantize(Decimal("0.01"))),
                "revenue": str(revenue),
                "margin": str(margin),
            }
        )
    return report


# =========================
# Events / Guards
# =========================
@event.listens_for(ProductStock, "before_insert")
def stock_before_insert(mapper, connection, target: ProductStock):  # pragma: no cover
    target.quantity = int(target.quantity or 0)
    target.reserved_quantity = int(target.reserved_quantity or 0)
    if target.quantity < 0 or target.reserved_quantity < 0:
        raise ValueError("Quantities must be non-negative")
    if target.reserved_quantity > target.quantity:
        raise ValueError("reserved_quantity cannot exceed quantity")
    if target.min_quantity is None:
        target.min_quantity = 0
    if target.max_quantity is not None and target.max_quantity < 0:
        raise ValueError("max_quantity must be >= 0")
    if target.min_quantity < 0:
        raise ValueError("min_quantity must be >= 0")
    if target.cost_price is not None:
        target.cost_price = Decimal(target.cost_price).quantize(Decimal("0.01"))


@event.listens_for(ProductStock, "before_update")
def stock_before_update(mapper, connection, target: ProductStock):  # pragma: no cover
    if target.quantity is not None and int(target.quantity) < 0:
        raise ValueError("quantity must be non-negative")
    if target.reserved_quantity is not None and int(target.reserved_quantity) < 0:
        raise ValueError("reserved_quantity must be non-negative")
    if int(target.reserved_quantity) > int(target.quantity):
        raise ValueError("reserved_quantity cannot exceed quantity")
    if target.cost_price is not None:
        target.cost_price = Decimal(target.cost_price).quantize(Decimal("0.01"))


@event.listens_for(StockMovement, "before_insert")
def movement_before_insert(mapper, connection, target: StockMovement):  # pragma: no cover
    allowed = {
        MovementType.IN.value,
        MovementType.OUT.value,
        MovementType.TRANSFER_IN.value,
        MovementType.TRANSFER_OUT.value,
        MovementType.ADJUSTMENT.value,
        MovementType.RESERVE.value,
        MovementType.RELEASE.value,
        MovementType.FULFILL.value,
    }
    if not target.movement_type or target.movement_type not in allowed:
        raise ValueError("Invalid movement_type")
    if target.movement_type and len(target.movement_type) > 32:
        raise ValueError("movement_type too long")
    target.quantity = int(target.quantity)
    target.previous_quantity = int(target.previous_quantity)
    target.new_quantity = int(target.new_quantity)
    if target.new_quantity < 0:
        raise ValueError("new_quantity must be non-negative")
    if target.created_at is None:
        target.created_at = datetime.utcnow()
    if target.updated_at is None:
        target.updated_at = target.created_at


@event.listens_for(StockMovement, "before_update")
def movement_before_update(mapper, connection, target: StockMovement):  # pragma: no cover
    if target.movement_type and len(target.movement_type) > 32:
        raise ValueError("movement_type too long")
    target.quantity = int(target.quantity)
    target.previous_quantity = int(target.previous_quantity)
    target.new_quantity = int(target.new_quantity)
    if target.new_quantity < 0:
        raise ValueError("new_quantity must be non-negative")
    target.updated_at = datetime.utcnow()


# =========================
# PostgreSQL-only: GIN index on working_hours::jsonb with jsonb_path_ops
# =========================
# Создаём индекс через чистый DDL, который выполняется ТОЛЬКО на PostgreSQL и
# не конфликтует с SQLite. Указан операторный класс jsonb_path_ops.
if not globals().get("_WH_GIN_IDX_DDL_ATTACHED", False):
    ddl = DDL(
        "CREATE INDEX IF NOT EXISTS ix_warehouses_working_hours_gin "
        "ON warehouses USING gin ((working_hours::jsonb) jsonb_path_ops)"
    ).execute_if(
        dialect="postgresql"
    )  # SQLAlchemy 2.x: ограничение по диалекту

    event.listen(Warehouse.__table__, "after_create", ddl)
    globals()["_WH_GIN_IDX_DDL_ATTACHED"] = True
