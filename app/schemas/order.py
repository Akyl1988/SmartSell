"""
Order Pydantic schemas.
"""

from decimal import Decimal

from pydantic import EmailStr, Field

from app.models.order import OrderSource, OrderStatus
from app.schemas.base import BaseCreateSchema, BaseResponseSchema, BaseUpdateSchema


class OrderItemCreate(BaseCreateSchema):
    """Schema for creating an order item"""

    product_id: int | None = None
    sku: str = Field(..., max_length=64)
    name: str = Field(..., max_length=500)
    unit_price: Decimal = Field(..., gt=0, decimal_places=2)
    quantity: int = Field(..., gt=0)
    notes: str | None = None


class OrderItemResponse(BaseResponseSchema):
    """Schema for order item response"""

    product_id: int | None
    sku: str
    name: str
    description: str | None
    unit_price: Decimal
    quantity: int
    total_price: Decimal
    product_image_url: str | None
    notes: str | None


class OrderCreate(BaseCreateSchema):
    """Schema for creating an order"""

    external_id: str | None = Field(None, max_length=128)
    source: OrderSource = OrderSource.MANUAL
    customer_phone: str | None = Field(None, max_length=32)
    customer_email: EmailStr | None = None
    customer_name: str | None = Field(None, max_length=255)
    customer_address: str | None = None
    delivery_method: str | None = Field(None, max_length=64)
    delivery_address: str | None = None
    delivery_date: str | None = None
    delivery_time: str | None = None
    notes: str | None = None
    items: list[OrderItemCreate] = Field(..., min_length=1)


class OrderUpdate(BaseUpdateSchema):
    """Schema for updating an order"""

    status: OrderStatus | None = None
    customer_phone: str | None = Field(None, max_length=32)
    customer_email: EmailStr | None = None
    customer_name: str | None = Field(None, max_length=255)
    customer_address: str | None = None
    delivery_method: str | None = Field(None, max_length=64)
    delivery_address: str | None = None
    delivery_date: str | None = None
    delivery_time: str | None = None
    notes: str | None = None
    internal_notes: str | None = None


class OrderResponse(BaseResponseSchema):
    """Schema for order response"""

    order_number: str
    external_id: str | None
    source: OrderSource
    status: OrderStatus
    customer_phone: str | None
    customer_email: str | None
    customer_name: str | None
    customer_address: str | None
    subtotal: Decimal
    tax_amount: Decimal
    shipping_amount: Decimal
    discount_amount: Decimal
    total_amount: Decimal
    currency: str
    delivery_method: str | None
    delivery_address: str | None
    delivery_date: str | None
    delivery_time: str | None
    notes: str | None
    internal_notes: str | None
    items_count: int
    company_id: int
    items: list[OrderItemResponse]


class OrderFilter(BaseCreateSchema):
    """Schema for order filtering"""

    search: str | None = None  # Search by order number, customer info
    status: OrderStatus | None = None
    source: OrderSource | None = None
    customer_phone: str | None = None
    date_from: str | None = None
    date_to: str | None = None
    min_amount: Decimal | None = None
    max_amount: Decimal | None = None


class OrderStatusUpdate(BaseCreateSchema):
    """Schema for order status update"""

    status: OrderStatus
    notes: str | None = None


class OrderSummary(BaseCreateSchema):
    """Schema for order summary statistics"""

    total_orders: int
    pending_orders: int
    confirmed_orders: int
    completed_orders: int
    cancelled_orders: int
    total_amount: Decimal
    average_order_amount: Decimal
