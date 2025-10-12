# app/models/order.py
"""
Order / OrderItem / OrderStatusHistory — управление заказами:
- статусная машина с историей
- привязка к платежам/счётам (billing), складу, продуктам, компании
- аналитика (таймсерии/агрегации), экспорт (CSV/Parquet), валидации
- бизнес-методы (добавление позиций, скидки, массовые апдейты)
- "ленивый" аудит/склад — без циклических зависимостей

ВНИМАНИЕ по времени:
- Для совместимости со всей кодовой базой используем UTC naive (datetime.utcnow) и DateTime без timezone=True.
- Если позже перейдёте на aware-ts — миграция одна и консистентная.

Импорт/загрузчик:
- __depends__ задан, чтобы наш _loader расставил порядок импорта заранее.
"""

from __future__ import annotations

import enum
from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Optional

from sqlalchemy import CheckConstraint, Column, DateTime
from sqlalchemy import Enum as SQLEnum
from sqlalchemy import (
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    delete,
    func,
    select,
    update,
)
from sqlalchemy.orm import relationship, validates

from app.models.base import Base

# Подсказываем автолоадеру порядок (короткие имена разрешены автолоадером):
__depends__ = ["billing", "company", "product", "warehouse"]


# ————————————————————————————————————————————————————————————————
# Лёгкие импорты в рантайме (только чтобы ORM видел классы — без жёстких зависимостей)
# ————————————————————————————————————————————————————————————————
if not TYPE_CHECKING:
    try:
        # обязательно billing, чтобы relationship("BillingPayment"/"BillingInvoice") резолвились
        from app.models.billing import BillingInvoice, BillingPayment  # noqa: F401
    except Exception:
        pass
    try:
        from app.models.company import Company  # noqa: F401
    except Exception:
        pass
    try:
        from app.models.product import Product  # noqa: F401
    except Exception:
        pass
    try:
        from app.models.warehouse import StockMovement  # noqa: F401
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Утилиты
# ---------------------------------------------------------------------------
def utc_now() -> datetime:
    """UTC naive timestamp."""
    return datetime.utcnow()


def _to_decimal(v: Any) -> Decimal:
    if v is None:
        return Decimal("0")
    if isinstance(v, Decimal):
        return v
    try:
        return Decimal(str(v))
    except Exception:
        return Decimal("0")


# ---------------------------------------------------------------------------
# Enums и разрешённые переходы
# ---------------------------------------------------------------------------
class OrderStatus(str, enum.Enum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    PAID = "paid"
    PROCESSING = "processing"
    SHIPPED = "shipped"
    DELIVERED = "delivered"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    REFUNDED = "refunded"


class OrderSource(str, enum.Enum):
    KASPI = "kaspi"
    WEBSITE = "website"
    MANUAL = "manual"
    API = "api"


ALLOWED_TRANSITIONS: dict[OrderStatus, set[OrderStatus]] = {
    OrderStatus.PENDING: {OrderStatus.CONFIRMED, OrderStatus.CANCELLED},
    OrderStatus.CONFIRMED: {OrderStatus.PAID, OrderStatus.CANCELLED},
    OrderStatus.PAID: {OrderStatus.PROCESSING, OrderStatus.REFUNDED, OrderStatus.CANCELLED},
    OrderStatus.PROCESSING: {OrderStatus.SHIPPED, OrderStatus.CANCELLED},
    OrderStatus.SHIPPED: {OrderStatus.DELIVERED, OrderStatus.CANCELLED},
    OrderStatus.DELIVERED: {OrderStatus.COMPLETED, OrderStatus.REFUNDED},
    OrderStatus.COMPLETED: set(),
    OrderStatus.CANCELLED: set(),
    OrderStatus.REFUNDED: set(),
}


# ---------------------------------------------------------------------------
# Order
# ---------------------------------------------------------------------------
class Order(Base):
    __tablename__ = "orders"

    id = Column(Integer, primary_key=True)
    created_at = Column(DateTime, default=utc_now, nullable=False, index=True)
    updated_at = Column(DateTime, default=utc_now, nullable=False, onupdate=utc_now, index=True)

    company_id = Column(ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True)

    order_number = Column(String(64), nullable=False, unique=True, index=True)
    external_id = Column(String(128), nullable=True, index=True)
    source = Column(SQLEnum(OrderSource), default=OrderSource.MANUAL, nullable=False, index=True)
    status = Column(SQLEnum(OrderStatus), default=OrderStatus.PENDING, nullable=False, index=True)

    customer_phone = Column(String(32), nullable=True, index=True)
    customer_email = Column(String(255), nullable=True)
    customer_name = Column(String(255), nullable=True)
    customer_address = Column(Text, nullable=True)

    subtotal = Column(Numeric(14, 2), nullable=False, default=0)
    tax_amount = Column(Numeric(14, 2), nullable=False, default=0)
    shipping_amount = Column(Numeric(14, 2), nullable=False, default=0)
    discount_amount = Column(Numeric(14, 2), nullable=False, default=0)
    total_amount = Column(Numeric(14, 2), nullable=False, default=0)
    currency = Column(String(8), default="KZT", nullable=False)

    delivery_method = Column(String(64), nullable=True)
    delivery_address = Column(Text, nullable=True)
    delivery_date = Column(String(32), nullable=True)
    delivery_time = Column(String(32), nullable=True)

    notes = Column(Text, nullable=True)
    internal_notes = Column(Text, nullable=True)

    # Relationships
    company = relationship("Company", back_populates="orders")
    items = relationship("OrderItem", back_populates="order", cascade="all, delete-orphan")

    payments = relationship(
        "BillingPayment",
        back_populates="order",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    invoice = relationship(
        "BillingInvoice",
        back_populates="order",
        uselist=False,
        lazy="joined",
    )

    stock_movements = relationship(
        "StockMovement",
        back_populates="order",
        cascade="all, delete-orphan",
    )

    status_history = relationship(
        "OrderStatusHistory",
        back_populates="order",
        cascade="all, delete-orphan",
        order_by="OrderStatusHistory.changed_at.asc()",
    )

    __table_args__ = (
        CheckConstraint("total_amount >= 0", name="ck_order_total_non_negative"),
        CheckConstraint(
            "subtotal >= 0 AND tax_amount >= 0 AND shipping_amount >= 0 AND discount_amount >= 0",
            name="ck_order_parts_non_negative",
        ),
        UniqueConstraint("order_number", name="uq_orders_order_number"),
        Index("ix_orders_company_status", "company_id", "status"),
        Index("ix_orders_source_created", "source", "created_at"),
    )

    # ------------------------ Валидации ------------------------
    @validates("order_number")
    def _validate_order_number(self, _k: str, v: str) -> str:
        v = (v or "").strip()
        if not v:
            raise ValueError("order_number must be non-empty")
        if len(v) > 64:
            raise ValueError("order_number length must be <= 64")
        return v

    # ------------------------ Свойства ------------------------
    @property
    def items_count(self) -> int:
        return sum(int(item.quantity or 0) for item in self.items)

    @property
    def is_paid(self) -> bool:
        return self.status in {
            OrderStatus.PAID,
            OrderStatus.PROCESSING,
            OrderStatus.SHIPPED,
            OrderStatus.DELIVERED,
            OrderStatus.COMPLETED,
        }

    @property
    def is_closed(self) -> bool:
        return self.status in {OrderStatus.CANCELLED, OrderStatus.COMPLETED, OrderStatus.REFUNDED}

    @property
    def is_cancelled(self) -> bool:
        return self.status == OrderStatus.CANCELLED

    @property
    def is_completed(self) -> bool:
        return self.status == OrderStatus.COMPLETED

    @property
    def is_refunded(self) -> bool:
        return self.status == OrderStatus.REFUNDED

    @property
    def is_active(self) -> bool:
        return not self.is_closed

    # ------------------------ CRUD / Query helpers ------------------------
    @staticmethod
    def get_by_order_number(session, order_number: str) -> Optional[Order]:
        return (
            session.query(Order).filter(Order.order_number == (order_number or "").strip()).first()
        )

    @staticmethod
    def create(
        session,
        *,
        company_id: int,
        order_number: str,
        source: OrderSource = OrderSource.MANUAL,
        customer_phone: Optional[str] = None,
        customer_email: Optional[str] = None,
        customer_name: Optional[str] = None,
        **extra_fields: Any,
    ) -> Order:
        obj = Order(
            company_id=company_id,
            order_number=order_number,
            source=source,
            customer_phone=customer_phone,
            customer_email=customer_email,
            customer_name=customer_name,
            **extra_fields,
        )
        session.add(obj)
        session.commit()
        return obj

    @staticmethod
    def create_from_cart(
        session,
        *,
        company_id: int,
        order_number: str,
        items: Sequence[dict[str, Any]],
        source: OrderSource = OrderSource.MANUAL,
        currency: str = "KZT",
        **customer_and_shipping: Any,
    ) -> Order:
        order = Order(
            company_id=company_id,
            order_number=order_number,
            source=source,
            currency=currency,
            **customer_and_shipping,
        )
        session.add(order)
        session.flush()
        for row in items or []:
            order.add_item(
                product_id=row.get("product_id"),
                sku=row.get("sku", ""),
                name=row.get("name", ""),
                unit_price=_to_decimal(row.get("unit_price", 0)),
                quantity=int(row.get("quantity") or 1),
                description=row.get("description"),
                image_url=row.get("image_url"),
                notes=row.get("notes"),
            )
        order.calculate_totals()
        session.commit()
        return order

    # ------------------------ Расчёты/мутации ------------------------
    def calculate_totals(self) -> None:
        subtotal = Decimal("0")
        for item in self.items:
            item.calculate_total()
            subtotal += _to_decimal(item.total_price)

        self.subtotal = subtotal
        total = (
            subtotal
            + _to_decimal(self.tax_amount)
            + _to_decimal(self.shipping_amount)
            - _to_decimal(self.discount_amount)
        ).quantize(Decimal("0.01"))
        if total < 0:
            total = Decimal("0")
        self.total_amount = total

    def apply_discount_absolute(self, amount: Decimal | float | int) -> None:
        amt = _to_decimal(amount)
        if amt < 0:
            raise ValueError("Discount must be non-negative")
        self.discount_amount = amt
        self.calculate_totals()

    def apply_discount_percent(self, pct: float) -> None:
        if pct < 0 or pct > 100:
            raise ValueError("pct must be in 0..100")
        self.discount_amount = (self.subtotal * _to_decimal(pct) / Decimal("100")).quantize(
            Decimal("0.01")
        )
        self.calculate_totals()

    def add_item(
        self,
        *,
        product_id: Optional[int],
        sku: str,
        name: str,
        unit_price: Decimal | float | int,
        quantity: int = 1,
        description: Optional[str] = None,
        image_url: Optional[str] = None,
        notes: Optional[str] = None,
    ) -> OrderItem:
        item = OrderItem(
            order=self,
            product_id=product_id,
            sku=sku,
            name=name,
            description=description,
            unit_price=_to_decimal(unit_price),
            quantity=int(quantity or 1),
            total_price=Decimal("0"),
            cost_price=Decimal("0"),
            product_image_url=image_url,
            notes=notes,
        )
        item.calculate_total()
        self.items.append(item)
        self.calculate_totals()
        return item

    # ------------------------ Аудит/склад ------------------------
    def add_audit_log(
        self,
        action: str,
        *,
        session=None,
        user_id: Optional[int] = None,
        description: Optional[str] = None,
        old_values: Optional[dict[str, Any]] = None,
        new_values: Optional[dict[str, Any]] = None,
        details: Optional[dict[str, Any]] = None,
        request_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
        source: Optional[str] = None,
        commit: bool = False,
    ) -> None:
        """Ленивый аудит без прямой зависимости от AuditLog."""
        try:
            from app.models.audit_log import AuditLog  # ленивый импорт
        except Exception:
            return

        AuditLog.safe_log(
            session=session,
            action=action,
            user_id=user_id,
            order_id=self.id,
            entity_type="order",
            entity_id=self.id,
            old_values=old_values,
            new_values=new_values,
            description=description,
            details=details,
            request_id=request_id,
            correlation_id=correlation_id,
            source=source,
            commit=commit,
        )

    def add_stock_movement(
        self,
        movement_type: str,
        product_id: int,
        quantity: int,
        user_id: Optional[int] = None,
        note: Optional[str] = None,
        *,
        timestamp: Optional[datetime] = None,
    ) -> None:
        """
        Ленивая запись движения склада (без жёсткой зависимости от модели склада).
        Поля согласованы с app.models.warehouse.StockMovement.
        """
        try:
            mapper = None
            for m in self.__class__.__mapper__.registry.mappers:  # type: ignore[attr-defined]
                if m.class_.__name__ == "StockMovement":
                    mapper = m
                    break
            if mapper is not None:
                StockMovementCls = mapper.class_
                movement = StockMovementCls(
                    order_id=self.id,
                    product_id=product_id,
                    movement_type=movement_type,
                    quantity=quantity,
                    user_id=user_id,
                    created_at=timestamp or utc_now(),
                    notes=note,
                )
                self.stock_movements.append(movement)
        except Exception:
            # безопасно игнорируем — не хотим ломать конфигурацию мапперов при неполной загрузке модулей
            pass

    # ------------------------ Статусы ------------------------
    def _append_status_history(
        self,
        old_status: OrderStatus,
        new_status: OrderStatus,
        user_id: Optional[int],
        note: Optional[str],
    ) -> None:
        self.status_history.append(
            OrderStatusHistory(
                order_id=self.id,
                old_status=old_status,
                new_status=new_status,
                changed_by=user_id,
                changed_at=utc_now(),
                note=note,
            )
        )

    def _enforce_transition(self, new_status: OrderStatus) -> None:
        allowed = ALLOWED_TRANSITIONS.get(self.status, set())
        if new_status not in allowed and new_status != self.status:
            raise ValueError(f"Transition {self.status.value} -> {new_status.value} is not allowed")

    def change_status(
        self,
        new_status: OrderStatus,
        user_id: Optional[int] = None,
        note: Optional[str] = None,
        *,
        session=None,
    ) -> None:
        old_status = self.status
        if new_status != old_status:
            self._enforce_transition(new_status)
            self.status = new_status
            self._append_status_history(old_status, new_status, user_id, note)
            self.add_audit_log(
                "order_status_change",
                session=session,
                user_id=user_id,
                description=note,
                old_values={"status": old_status.value},
                new_values={"status": new_status.value},
                commit=False,
            )

    # helpers
    def confirm(self, user_id: Optional[int] = None, note: Optional[str] = None, *, session=None):
        self.change_status(OrderStatus.CONFIRMED, user_id, note, session=session)

    def mark_paid(self, user_id: Optional[int] = None, note: Optional[str] = None, *, session=None):
        self.change_status(OrderStatus.PAID, user_id, note, session=session)

    def start_processing(
        self, user_id: Optional[int] = None, note: Optional[str] = None, *, session=None
    ):
        self.change_status(OrderStatus.PROCESSING, user_id, note, session=session)

    def ship(self, user_id: Optional[int] = None, note: Optional[str] = None, *, session=None):
        self.change_status(OrderStatus.SHIPPED, user_id, note, session=session)

    def deliver(self, user_id: Optional[int] = None, note: Optional[str] = None, *, session=None):
        self.change_status(OrderStatus.DELIVERED, user_id, note, session=session)

    def complete(self, user_id: Optional[int] = None, note: Optional[str] = None, *, session=None):
        self.change_status(OrderStatus.COMPLETED, user_id, note, session=session)

    def cancel(self, user_id: Optional[int] = None, note: Optional[str] = None, *, session=None):
        self.change_status(OrderStatus.CANCELLED, user_id, note, session=session)

    def refund(self, user_id: Optional[int] = None, note: Optional[str] = None, *, session=None):
        self.change_status(OrderStatus.REFUNDED, user_id, note, session=session)

    # ------------------------ Аналитика/BI ------------------------
    @staticmethod
    async def revenue_time_series_async(
        session,
        *,
        company_id: int,
        bucket: str = "day",
        date_from: Optional[datetime] = None,
        date_to: Optional[datetime] = None,
        only_status: Optional[OrderStatus] = None,
    ) -> list[tuple[str, float]]:
        """
        Возвращает [(period_label, revenue_sum)], сгруппировано по часам или дням.
        Требует Postgres (date_trunc/to_char). На других СУБД может потребоваться адаптация.
        """
        if bucket not in {"hour", "day"}:
            raise ValueError("bucket must be 'hour' or 'day'")
        ts_col = Order.created_at
        bucket_expr = func.date_trunc("hour" if bucket == "hour" else "day", ts_col)
        fmt = "%Y-%m-%d %H:00" if bucket == "hour" else "%Y-%m-%d"

        conds = [Order.company_id == company_id]
        if date_from:
            conds.append(Order.created_at >= date_from)
        if date_to:
            conds.append(Order.created_at < date_to)
        if only_status:
            conds.append(Order.status == only_status)

        rows = await session.execute(
            select(func.to_char(bucket_expr, fmt), func.coalesce(func.sum(Order.total_amount), 0))
            .where(*conds)
            .group_by(bucket_expr)
            .order_by(bucket_expr.asc())
        )
        return [(str(label), float(rev)) for label, rev in rows.all()]

    @staticmethod
    async def analytics_by_period_dataframe_async(
        session,
        *,
        bucket: str = "day",
        company_ids: Optional[Sequence[int]] = None,
        date_from: Optional[datetime] = None,
        date_to: Optional[datetime] = None,
        status_filter: Optional[Sequence[OrderStatus]] = None,
    ):
        """
        Возвращает pandas.DataFrame с колонками: period, company_id, orders_count, orders_sum
        """
        try:
            import pandas as pd  # type: ignore
        except Exception as e:  # pragma: no cover
            raise ImportError("pandas is required for analytics_by_period_dataframe_async()") from e

        bucket = bucket.lower().strip()
        if bucket not in {"day", "week", "month"}:
            raise ValueError("bucket must be 'day' or 'week' or 'month'")

        if bucket == "day":
            bucket_expr = func.date_trunc("day", Order.created_at)
            fmt = "%Y-%m-%d"
        elif bucket == "week":
            bucket_expr = func.date_trunc("week", Order.created_at)
            fmt = "%G-W%V"
        else:
            bucket_expr = func.date_trunc("month", Order.created_at)
            fmt = "%Y-%m"

        conds = []
        if company_ids:
            conds.append(Order.company_id.in_(list(company_ids)))
        if date_from:
            conds.append(Order.created_at >= date_from)
        if date_to:
            conds.append(Order.created_at < date_to)
        if status_filter:
            conds.append(Order.status.in_(list(status_filter)))

        rows = await session.execute(
            select(
                func.to_char(bucket_expr, fmt).label("period"),
                Order.company_id,
                func.count(Order.id).label("orders_count"),
                func.coalesce(func.sum(Order.total_amount), 0).label("orders_sum"),
            )
            .where(*conds)
            .group_by(bucket_expr, Order.company_id)
            .order_by(bucket_expr.asc(), Order.company_id.asc())
        )
        data = [
            {"period": p, "company_id": cid, "orders_count": int(c), "orders_sum": float(s)}
            for p, cid, c, s in rows.all()
        ]
        return pd.DataFrame(data)

    @staticmethod
    async def analytics_by_period_csv_async(
        session,
        *,
        path: str,
        bucket: str = "day",
        company_ids: Optional[Sequence[int]] = None,
        date_from: Optional[datetime] = None,
        date_to: Optional[datetime] = None,
        status_filter: Optional[Sequence[OrderStatus]] = None,
        index: bool = False,
        **to_csv_kwargs: Any,
    ) -> str:
        df = await Order.analytics_by_period_dataframe_async(
            session,
            bucket=bucket,
            company_ids=company_ids,
            date_from=date_from,
            date_to=date_to,
            status_filter=status_filter,
        )
        df.to_csv(path, index=index, **to_csv_kwargs)
        return path

    @staticmethod
    async def analytics_by_period_parquet_async(
        session,
        *,
        path: str,
        bucket: str = "day",
        company_ids: Optional[Sequence[int]] = None,
        date_from: Optional[datetime] = None,
        date_to: Optional[datetime] = None,
        status_filter: Optional[Sequence[OrderStatus]] = None,
        engine: str = "pyarrow",
        **kwargs: Any,
    ) -> str:
        df = await Order.analytics_by_period_dataframe_async(
            session,
            bucket=bucket,
            company_ids=company_ids,
            date_from=date_from,
            date_to=date_to,
            status_filter=status_filter,
        )
        df.to_parquet(path, engine=engine, **kwargs)
        return path

    @staticmethod
    async def bulk_update_status_async(
        session,
        order_ids: Sequence[int],
        *,
        new_status: OrderStatus,
        user_id: Optional[int] = None,
        note: Optional[str] = None,
    ) -> int:
        if not order_ids:
            return 0
        now = utc_now()
        res = await session.execute(
            update(Order).where(Order.id.in_(order_ids)).values(status=new_status, updated_at=now)
        )
        count = int(res.rowcount or 0)

        # Ленивый аудит
        try:
            from app.models.audit_log import AuditLog

            for oid in order_ids:
                AuditLog.safe_log(
                    session=session,
                    action="order_status_bulk_update",
                    user_id=user_id,
                    order_id=oid,
                    entity_type="order",
                    entity_id=oid,
                    old_values=None,
                    new_values={"status": new_status.value},
                    description=note,
                    commit=False,
                )
        except Exception:
            pass

        return count

    # ------------------------ Экспорт ------------------------
    @staticmethod
    def export_query(
        *,
        company_id: Optional[int] = None,
        status_in: Optional[Sequence[OrderStatus]] = None,
        source_in: Optional[Sequence[OrderSource]] = None,
        date_from: Optional[datetime] = None,
        date_to: Optional[datetime] = None,
        min_total: Optional[Decimal] = None,
        max_total: Optional[Decimal] = None,
    ):
        conds = []
        if company_id is not None:
            conds.append(Order.company_id == company_id)
        if status_in:
            conds.append(Order.status.in_(list(status_in)))
        if source_in:
            conds.append(Order.source.in_(list(source_in)))
        if date_from:
            conds.append(Order.created_at >= date_from)
        if date_to:
            conds.append(Order.created_at < date_to)
        if min_total is not None:
            conds.append(Order.total_amount >= _to_decimal(min_total))
        if max_total is not None:
            conds.append(Order.total_amount <= _to_decimal(max_total))

        return (
            select(
                Order.id,
                Order.order_number,
                Order.company_id,
                Order.source,
                Order.status,
                Order.total_amount,
                Order.currency,
                Order.created_at,
                Order.updated_at,
                Order.customer_phone,
                Order.customer_email,
                Order.customer_name,
            )
            .where(*conds if conds else [True])
            .order_by(Order.created_at.desc())
        )

    @staticmethod
    async def export_as_dicts_async(session, **kwargs) -> list[dict[str, Any]]:
        rows = (await session.execute(Order.export_query(**kwargs))).all()
        out: list[dict[str, Any]] = []
        for r in rows:
            (
                oid,
                onum,
                cid,
                source,
                status,
                total,
                currency,
                created_at,
                updated_at,
                phone,
                email,
                name,
            ) = r
            out.append(
                {
                    "order_id": oid,
                    "order_number": onum,
                    "company_id": cid,
                    "source": source.value if isinstance(source, OrderSource) else str(source),
                    "status": status.value if isinstance(status, OrderStatus) else str(status),
                    "total_amount": str(total) if total is not None else None,
                    "currency": currency,
                    "created_at": created_at.isoformat() if created_at else None,
                    "updated_at": updated_at.isoformat() if updated_at else None,
                    "customer_phone": phone,
                    "customer_email": email,
                    "customer_name": name,
                }
            )
        return out

    @staticmethod
    async def export_to_csv_async(session, *, path: str, **kwargs) -> str:
        data = await Order.export_as_dicts_async(session, **kwargs)
        import csv  # stdlib

        if not data:
            header = [
                "order_id",
                "order_number",
                "company_id",
                "source",
                "status",
                "total_amount",
                "currency",
                "created_at",
                "updated_at",
                "customer_phone",
                "customer_email",
                "customer_name",
            ]
            with open(path, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(header)
            return path

        header = list(data[0].keys())
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=header)
            writer.writeheader()
            for row in data:
                writer.writerow(row)
        return path

    def __repr__(self):
        return f"<Order(id={self.id}, number='{self.order_number}', status='{self.status}')>"


# ---------------------------------------------------------------------------
# OrderItem
# ---------------------------------------------------------------------------
class OrderItem(Base):
    __tablename__ = "order_items"

    id = Column(Integer, primary_key=True)
    order_id = Column(ForeignKey("orders.id", ondelete="CASCADE"), nullable=False, index=True)

    product_id = Column(
        ForeignKey("products.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )

    sku = Column(String(64), nullable=False, index=True)
    name = Column(String(500), nullable=False)
    description = Column(Text, nullable=True)

    unit_price = Column(Numeric(14, 2), nullable=False)
    quantity = Column(Integer, nullable=False, default=1)
    total_price = Column(Numeric(14, 2), nullable=False)
    cost_price = Column(Numeric(14, 2), nullable=False, default=0)

    product_image_url = Column(String(1024), nullable=True)
    notes = Column(Text, nullable=True)

    order = relationship("Order", back_populates="items")
    product = relationship("Product", back_populates="order_items")

    __table_args__ = (
        CheckConstraint("quantity > 0", name="ck_order_item_quantity_positive"),
        CheckConstraint(
            "unit_price >= 0 AND total_price >= 0 AND cost_price >= 0",
            name="ck_order_item_price_non_negative",
        ),
        Index("ix_order_items_order_sku", "order_id", "sku"),
    )

    @validates("sku")
    def _validate_sku(self, _k: str, v: str) -> str:
        v = (v or "").strip()
        if not v:
            raise ValueError("SKU must be non-empty")
        if len(v) > 64:
            raise ValueError("SKU length must be <= 64")
        return v

    def calculate_total(self):
        qty = int(self.quantity or 0)
        unit = _to_decimal(self.unit_price)
        self.total_price = (unit * _to_decimal(qty)).quantize(Decimal("0.01"))

    @property
    def is_discounted(self) -> bool:
        expected = (_to_decimal(self.unit_price) * _to_decimal(self.quantity)).quantize(
            Decimal("0.01")
        )
        actual = _to_decimal(self.total_price).quantize(Decimal("0.01"))
        return actual < expected

    def _proportion(self) -> Decimal:
        order = self.order
        if not order:
            return Decimal("0")
        subtotal = _to_decimal(order.subtotal)
        if subtotal <= 0:
            return Decimal("0")
        return (_to_decimal(self.total_price) / subtotal).quantize(Decimal("0.0000001"))

    @property
    def allocated_tax_amount(self) -> Decimal:
        order = self.order
        if not order:
            return Decimal("0")
        return (_to_decimal(order.tax_amount) * self._proportion()).quantize(Decimal("0.01"))

    @property
    def allocated_discount_amount(self) -> Decimal:
        order = self.order
        if not order:
            return Decimal("0")
        return (_to_decimal(order.discount_amount) * self._proportion()).quantize(Decimal("0.01"))

    @property
    def allocated_shipping_amount(self) -> Decimal:
        order = self.order
        if not order:
            return Decimal("0")
        return (_to_decimal(order.shipping_amount) * self._proportion()).quantize(Decimal("0.01"))

    @property
    def gross_total_with_allocations(self) -> Decimal:
        return (
            _to_decimal(self.total_price)
            + self.allocated_tax_amount
            + self.allocated_shipping_amount
            - self.allocated_discount_amount
        ).quantize(Decimal("0.01"))

    @property
    def margin_amount(self) -> Decimal:
        revenue_net = (_to_decimal(self.total_price) - self.allocated_discount_amount).quantize(
            Decimal("0.01")
        )
        cost = (_to_decimal(self.cost_price) * _to_decimal(self.quantity)).quantize(Decimal("0.01"))
        return (revenue_net - cost).quantize(Decimal("0.01"))

    @property
    def margin_rate(self) -> Optional[Decimal]:
        revenue_net = (_to_decimal(self.total_price) - self.allocated_discount_amount).quantize(
            Decimal("0.01")
        )
        if revenue_net <= 0:
            return None
        return (self.margin_amount / revenue_net).quantize(Decimal("0.0001"))

    def __repr__(self):
        return f"<OrderItem(id={self.id}, sku='{self.sku}', quantity={self.quantity})>"


# ---------------------------------------------------------------------------
# OrderStatusHistory
# ---------------------------------------------------------------------------
class OrderStatusHistory(Base):
    __tablename__ = "order_status_history"

    id = Column(Integer, primary_key=True)
    order_id = Column(ForeignKey("orders.id", ondelete="CASCADE"), nullable=False, index=True)
    old_status = Column(SQLEnum(OrderStatus), nullable=False)
    new_status = Column(SQLEnum(OrderStatus), nullable=False)
    changed_by = Column(Integer, nullable=True, index=True)  # user_id or staff_id
    changed_at = Column(DateTime, default=utc_now, nullable=False, index=True)
    note = Column(Text, nullable=True)

    order = relationship("Order", back_populates="status_history")

    # -------- Снимки истории --------
    @staticmethod
    async def snapshot_for_order_async(session, order_id: int) -> list[dict[str, Any]]:
        rows = await session.execute(
            select(
                OrderStatusHistory.id,
                OrderStatusHistory.order_id,
                OrderStatusHistory.old_status,
                OrderStatusHistory.new_status,
                OrderStatusHistory.changed_by,
                OrderStatusHistory.changed_at,
                OrderStatusHistory.note,
            )
            .where(OrderStatusHistory.order_id == order_id)
            .order_by(OrderStatusHistory.changed_at.asc(), OrderStatusHistory.id.asc())
        )
        out: list[dict[str, Any]] = []
        for rid, oid, old_s, new_s, by, at, note in rows.all():
            out.append(
                {
                    "id": rid,
                    "order_id": oid,
                    "old_status": old_s.value if isinstance(old_s, OrderStatus) else str(old_s),
                    "new_status": new_s.value if isinstance(new_s, OrderStatus) else str(new_s),
                    "changed_by": by,
                    "changed_at": at.isoformat() if at else None,
                    "note": note,
                }
            )
        return out

    # -------- Батч-восстановление истории (compliance) --------
    @staticmethod
    async def batch_restore_missing_async(
        session,
        *,
        order_ids: Sequence[int],
        created_event_note: str = "history restored: created",
        ensure_initial_transition: bool = True,
    ) -> int:
        """
        Восстанавливает минимально необходимую историю:
        - Если по заказу нет записей истории вовсе — создаёт первый event old=pending, new=<текущий статус>
          (или pending->pending, если ensure_initial_transition=False).
        - Если первая запись истории не начинается с pending — добавляет начальный pending-><first.old_status>.

        Возвращает количество добавленных записей.
        """
        if not order_ids:
            return 0

        orders_rows = await session.execute(
            select(Order.id, Order.status, Order.created_at).where(Order.id.in_(list(order_ids)))
        )
        orders = {oid: (status, created_at) for oid, status, created_at in orders_rows.all()}

        # Первая запись истории (если есть) по каждому заказу
        hist_first_rows = await session.execute(
            select(
                OrderStatusHistory.order_id,
                OrderStatusHistory.id,
                OrderStatusHistory.old_status,
                OrderStatusHistory.new_status,
                OrderStatusHistory.changed_at,
            )
            .where(OrderStatusHistory.order_id.in_(list(order_ids)))
            .order_by(
                OrderStatusHistory.order_id.asc(),
                OrderStatusHistory.changed_at.asc(),
                OrderStatusHistory.id.asc(),
            )
        )
        first_map: dict[int, tuple[int, OrderStatus, OrderStatus, datetime]] = {}
        for oid, hid, old_s, new_s, changed_at in hist_first_rows.all():
            if oid not in first_map:
                first_map[oid] = (hid, old_s, new_s, changed_at)

        added = 0
        now = utc_now()

        for oid in order_ids:
            status, created_at = orders.get(oid, (OrderStatus.PENDING, now))
            if oid not in first_map:
                # истории нет вообще — создаём начальную запись
                old_s = OrderStatus.PENDING
                new_s = status if ensure_initial_transition else OrderStatus.PENDING
                session.add(
                    OrderStatusHistory(
                        order_id=oid,
                        old_status=old_s,
                        new_status=new_s,
                        changed_by=None,
                        changed_at=created_at or now,
                        note=created_event_note,
                    )
                )
                added += 1
                continue

            # история есть — проверяем первую запись (должна начинаться с pending)
            _, first_old, first_new, first_at = first_map[oid]
            if first_old != OrderStatus.PENDING:
                session.add(
                    OrderStatusHistory(
                        order_id=oid,
                        old_status=OrderStatus.PENDING,
                        new_status=first_old,
                        changed_by=None,
                        changed_at=(created_at or first_at or now),
                        note="history restored: initial pending",
                    )
                )
                added += 1

        return added

    # -------- Утилиты модерации истории --------
    @staticmethod
    async def delete_history_for_orders_async(session, order_ids: Sequence[int]) -> int:
        if not order_ids:
            return 0
        res = await session.execute(
            delete(OrderStatusHistory).where(OrderStatusHistory.order_id.in_(list(order_ids)))
        )
        return int(res.rowcount or 0)

    def __repr__(self):
        return (
            f"<OrderStatusHistory(order_id={self.order_id}, {self.old_status}->{self.new_status})>"
        )
