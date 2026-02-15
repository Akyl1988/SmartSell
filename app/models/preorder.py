"""Preorder domain model."""

from __future__ import annotations

import enum
from datetime import datetime
from decimal import Decimal

from sqlalchemy import CheckConstraint, Column, DateTime, ForeignKey, Index, Integer, Numeric, String, Text
from sqlalchemy import Enum as SQLEnum
from sqlalchemy.orm import relationship

from app.models.base import BaseModel


class PreorderStatus(str, enum.Enum):
    CREATED = "created"
    CONFIRMED = "confirmed"
    CANCELLED = "cancelled"
    CONVERTED = "converted"


_ALLOWED_TRANSITIONS: dict[PreorderStatus, set[PreorderStatus]] = {
    PreorderStatus.CREATED: {PreorderStatus.CONFIRMED, PreorderStatus.CANCELLED},
    PreorderStatus.CONFIRMED: {PreorderStatus.CANCELLED, PreorderStatus.CONVERTED},
    PreorderStatus.CANCELLED: set(),
    PreorderStatus.CONVERTED: set(),
}


_STATUS_ENUM = SQLEnum(
    PreorderStatus,
    name="preorder_status",
    values_callable=lambda obj: [e.value for e in obj],
)


class Preorder(BaseModel):
    __tablename__ = "preorders"

    company_id = Column(ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True)
    product_id = Column(ForeignKey("products.id", ondelete="RESTRICT"), nullable=False, index=True)
    qty = Column(Integer, nullable=False)

    customer_name = Column(String(255), nullable=True)
    customer_phone = Column(String(32), nullable=True)
    comment = Column(Text, nullable=True)

    status = Column(_STATUS_ENUM, nullable=False, default=PreorderStatus.CREATED)

    preorder_until_snapshot = Column(DateTime, nullable=True)
    deposit_snapshot = Column(Numeric(14, 2), nullable=True)

    converted_order_id = Column(ForeignKey("orders.id", ondelete="SET NULL"), nullable=True, index=True)

    product = relationship("Product")
    company = relationship("Company")
    converted_order = relationship("Order")

    __table_args__ = (
        CheckConstraint("qty > 0", name="ck_preorders_qty_positive"),
        Index("ix_preorders_company_created", "company_id", "created_at"),
        Index("ix_preorders_company_status", "company_id", "status"),
        Index("ix_preorders_company_product", "company_id", "product_id"),
    )

    def change_status(self, new_status: PreorderStatus) -> None:
        allowed = _ALLOWED_TRANSITIONS.get(self.status, set())
        if new_status not in allowed and new_status != self.status:
            raise ValueError(f"Transition {self.status.value} -> {new_status.value} is not allowed")
        self.status = new_status

    def confirm(self) -> None:
        self.change_status(PreorderStatus.CONFIRMED)

    def cancel(self) -> None:
        self.change_status(PreorderStatus.CANCELLED)

    def mark_converted(self, order_id: int) -> None:
        self.converted_order_id = order_id
        self.change_status(PreorderStatus.CONVERTED)

    def snapshot_from_product(self, *, preorder_until: int | None, deposit: Decimal | None) -> None:
        if preorder_until is None:
            self.preorder_until_snapshot = None
        else:
            self.preorder_until_snapshot = datetime.utcfromtimestamp(int(preorder_until))
        self.deposit_snapshot = deposit
