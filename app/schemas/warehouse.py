"""
Warehouse Pydantic schemas.
"""

from typing import Optional

from pydantic import EmailStr, Field

from app.schemas.base import BaseCreateSchema, BaseResponseSchema, BaseUpdateSchema


class WarehouseCreate(BaseCreateSchema):
    """Schema for creating a warehouse"""

    name: str = Field(..., min_length=1, max_length=255)
    code: Optional[str] = Field(None, max_length=32)
    address: Optional[str] = None
    city: Optional[str] = Field(None, max_length=100)
    region: Optional[str] = Field(None, max_length=100)
    postal_code: Optional[str] = Field(None, max_length=20)
    phone: Optional[str] = Field(None, max_length=32)
    email: Optional[EmailStr] = None
    manager_name: Optional[str] = Field(None, max_length=255)
    working_hours: Optional[dict] = None
    is_main: bool = False


class WarehouseUpdate(BaseUpdateSchema):
    """Schema for updating a warehouse"""

    name: Optional[str] = Field(None, min_length=1, max_length=255)
    code: Optional[str] = Field(None, max_length=32)
    address: Optional[str] = None
    city: Optional[str] = Field(None, max_length=100)
    region: Optional[str] = Field(None, max_length=100)
    postal_code: Optional[str] = Field(None, max_length=20)
    phone: Optional[str] = Field(None, max_length=32)
    email: Optional[EmailStr] = None
    manager_name: Optional[str] = Field(None, max_length=255)
    working_hours: Optional[dict] = None
    is_active: Optional[bool] = None
    is_main: Optional[bool] = None


class WarehouseResponse(BaseResponseSchema):
    """Schema for warehouse response"""

    name: str
    code: Optional[str]
    address: Optional[str]
    city: Optional[str]
    region: Optional[str]
    postal_code: Optional[str]
    phone: Optional[str]
    email: Optional[str]
    manager_name: Optional[str]
    working_hours: Optional[dict]
    is_active: bool
    is_main: bool
    company_id: int


class ProductStockCreate(BaseCreateSchema):
    """Schema for creating product stock"""

    product_id: int
    warehouse_id: int
    quantity: int = Field(..., ge=0)
    min_quantity: int = Field(default=0, ge=0)
    max_quantity: Optional[int] = Field(None, gt=0)
    location: Optional[str] = Field(None, max_length=100)


class ProductStockUpdate(BaseUpdateSchema):
    """Schema for updating product stock"""

    quantity: Optional[int] = Field(None, ge=0)
    min_quantity: Optional[int] = Field(None, ge=0)
    max_quantity: Optional[int] = Field(None, gt=0)
    location: Optional[str] = Field(None, max_length=100)


class ProductStockResponse(BaseResponseSchema):
    """Schema for product stock response"""

    product_id: int
    warehouse_id: int
    quantity: int
    reserved_quantity: int
    min_quantity: int
    max_quantity: Optional[int]
    location: Optional[str]
    available_quantity: int
    is_low_stock: bool
    product_sku: Optional[str]
    product_name: Optional[str]
    warehouse_name: Optional[str]


class StockMovementCreate(BaseCreateSchema):
    """Schema for creating stock movement"""

    stock_id: int
    movement_type: str = Field(..., max_length=32)  # in, out, transfer, adjustment
    quantity: int  # Positive for in, negative for out
    reference_type: Optional[str] = Field(None, max_length=32)
    reference_id: Optional[int] = None
    reason: Optional[str] = Field(None, max_length=255)
    notes: Optional[str] = None


class StockMovementResponse(BaseResponseSchema):
    """Schema for stock movement response"""

    stock_id: int
    movement_type: str
    quantity: int
    previous_quantity: int
    new_quantity: int
    reference_type: Optional[str]
    reference_id: Optional[int]
    reason: Optional[str]
    notes: Optional[str]
    user_id: Optional[int]
    product_sku: Optional[str]
    product_name: Optional[str]
    warehouse_name: Optional[str]


class StockTransfer(BaseCreateSchema):
    """Schema for stock transfer between warehouses"""

    product_id: int
    from_warehouse_id: int
    to_warehouse_id: int
    quantity: int = Field(..., gt=0)
    reason: Optional[str] = Field(None, max_length=255)
    notes: Optional[str] = None


class StockAdjustment(BaseCreateSchema):
    """Schema for stock adjustment"""

    adjustments: list[dict] = Field(
        ..., min_items=1
    )  # List of {product_id, warehouse_id, new_quantity, reason}
    notes: Optional[str] = None


class WarehouseStats(BaseCreateSchema):
    """Schema for warehouse statistics"""

    total_products: int
    total_stock: int
    low_stock_products: int
    out_of_stock_products: int
    total_value: float
