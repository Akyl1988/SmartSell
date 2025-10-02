"""
Order Pydantic schemas.
"""

from decimal import Decimal
from typing import Optional

from pydantic import EmailStr, Field

from app.models.order import OrderSource, OrderStatus
from app.schemas.base import BaseCreateSchema, BaseResponseSchema, BaseUpdateSchema


class OrderItemCreate(BaseCreateSchema):
    """Schema for creating an order item"""

    product_id: Optional[int] = None
    sku: str = Field(..., max_length=64)
    name: str = Field(..., max_length=500)
    unit_price: Decimal = Field(..., gt=0, decimal_places=2)
    quantity: int = Field(..., gt=0)
    notes: Optional[str] = None


class OrderItemResponse(BaseResponseSchema):
    """Schema for order item response"""

    product_id: Optional[int]
    sku: str
    name: str
    description: Optional[str]
    unit_price: Decimal
    quantity: int
    total_price: Decimal
    product_image_url: Optional[str]
    notes: Optional[str]


class OrderCreate(BaseCreateSchema):
    """Schema for creating an order"""

    external_id: Optional[str] = Field(None, max_length=128)
    source: OrderSource = OrderSource.MANUAL
    customer_phone: Optional[str] = Field(None, max_length=32)
    customer_email: Optional[EmailStr] = None
    customer_name: Optional[str] = Field(None, max_length=255)
    customer_address: Optional[str] = None
    delivery_method: Optional[str] = Field(None, max_length=64)
    delivery_address: Optional[str] = None
    delivery_date: Optional[str] = None
    delivery_time: Optional[str] = None
    notes: Optional[str] = None
    items: list[OrderItemCreate] = Field(..., min_items=1)


class OrderUpdate(BaseUpdateSchema):
    """Schema for updating an order"""

    status: Optional[OrderStatus] = None
    customer_phone: Optional[str] = Field(None, max_length=32)
    customer_email: Optional[EmailStr] = None
    customer_name: Optional[str] = Field(None, max_length=255)
    customer_address: Optional[str] = None
    delivery_method: Optional[str] = Field(None, max_length=64)
    delivery_address: Optional[str] = None
    delivery_date: Optional[str] = None
    delivery_time: Optional[str] = None
    notes: Optional[str] = None
    internal_notes: Optional[str] = None


class OrderResponse(BaseResponseSchema):
    """Schema for order response"""

    order_number: str
    external_id: Optional[str]
    source: OrderSource
    status: OrderStatus
    customer_phone: Optional[str]
    customer_email: Optional[str]
    customer_name: Optional[str]
    customer_address: Optional[str]
    subtotal: Decimal
    tax_amount: Decimal
    shipping_amount: Decimal
    discount_amount: Decimal
    total_amount: Decimal
    currency: str
    delivery_method: Optional[str]
    delivery_address: Optional[str]
    delivery_date: Optional[str]
    delivery_time: Optional[str]
    notes: Optional[str]
    internal_notes: Optional[str]
    items_count: int
    company_id: int
    items: list[OrderItemResponse]


class OrderFilter(BaseCreateSchema):
    """Schema for order filtering"""

    search: Optional[str] = None  # Search by order number, customer info
    status: Optional[OrderStatus] = None
    source: Optional[OrderSource] = None
    customer_phone: Optional[str] = None
    date_from: Optional[str] = None
    date_to: Optional[str] = None
    min_amount: Optional[Decimal] = None
    max_amount: Optional[Decimal] = None


class OrderStatusUpdate(BaseCreateSchema):
    """Schema for order status update"""

    status: OrderStatus
    notes: Optional[str] = None


class OrderSummary(BaseCreateSchema):
    """Schema for order summary statistics"""

    total_orders: int
    pending_orders: int
    confirmed_orders: int
    completed_orders: int
    cancelled_orders: int
    total_amount: Decimal
    average_order_amount: Decimal
