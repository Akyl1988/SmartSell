"""Preorder domain model."""

from __future__ import annotations

import enum

from sqlalchemy import Column, DateTime, ForeignKey, Index, Integer, Numeric, String, Text
from sqlalchemy import Enum as SQLEnum
from sqlalchemy.orm import relationship

from app.models.base import BaseModel


class PreorderStatus(str, enum.Enum):
    NEW = "new"
    CONFIRMED = "confirmed"
    CANCELLED = "cancelled"
    FULFILLED = "fulfilled"


_ALLOWED_TRANSITIONS: dict[PreorderStatus, set[PreorderStatus]] = {
    PreorderStatus.NEW: {PreorderStatus.CONFIRMED, PreorderStatus.CANCELLED},
    PreorderStatus.CONFIRMED: {PreorderStatus.CANCELLED, PreorderStatus.FULFILLED},
    PreorderStatus.CANCELLED: set(),
    PreorderStatus.FULFILLED: set(),
}


_STATUS_ENUM = SQLEnum(
    PreorderStatus,
    name="preorder_status",
    values_callable=lambda obj: [e.value for e in obj],
)


class Preorder(BaseModel):
    __tablename__ = "preorders"

    company_id = Column(ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True)
    status = Column(_STATUS_ENUM, nullable=False, default=PreorderStatus.NEW)
    currency = Column(String(8), nullable=False, default="KZT")
    total = Column(Numeric(14, 2), nullable=True)
    customer_name = Column(String(255), nullable=True)
    customer_phone = Column(String(32), nullable=True)
    notes = Column(Text, nullable=True)
    created_by_user_id = Column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    fulfilled_order_id = Column(ForeignKey("orders.id", ondelete="SET NULL"), nullable=True, index=True)
    fulfilled_at = Column(DateTime, nullable=True)

    company = relationship("Company")
    created_by_user = relationship("User")
    fulfilled_order = relationship("Order")
    items = relationship("PreorderItem", back_populates="preorder", cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_preorders_company_created", "company_id", "created_at"),
        Index("ix_preorders_company_status", "company_id", "status"),
        Index("ix_preorders_fulfilled_order", "fulfilled_order_id"),
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

    def fulfill(self) -> None:
        self.change_status(PreorderStatus.FULFILLED)


class PreorderItem(BaseModel):
    __tablename__ = "preorder_items"

    preorder_id = Column(ForeignKey("preorders.id", ondelete="CASCADE"), nullable=False, index=True)
    product_id = Column(ForeignKey("products.id", ondelete="SET NULL"), nullable=True, index=True)
    sku = Column(String(100), nullable=True)
    name = Column(String(255), nullable=True)
    qty = Column(Integer, nullable=False)
    price = Column(Numeric(14, 2), nullable=True)

    preorder = relationship("Preorder", back_populates="items")
    product = relationship("Product")

    __table_args__ = (
        Index("ix_preorder_items_preorder", "preorder_id"),
        Index("ix_preorder_items_product", "product_id"),
    )
