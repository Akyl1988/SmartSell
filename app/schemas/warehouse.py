"""
Warehouse Pydantic schemas.
"""


from pydantic import EmailStr, Field

from app.schemas.base import BaseCreateSchema, BaseResponseSchema, BaseUpdateSchema


class WarehouseCreate(BaseCreateSchema):
    """Schema for creating a warehouse"""

    name: str = Field(..., min_length=1, max_length=255)
    code: str | None = Field(None, max_length=32)
    address: str | None = None
    city: str | None = Field(None, max_length=100)
    region: str | None = Field(None, max_length=100)
    postal_code: str | None = Field(None, max_length=20)
    phone: str | None = Field(None, max_length=32)
    email: EmailStr | None = None
    manager_name: str | None = Field(None, max_length=255)
    working_hours: dict | None = None
    is_main: bool = False


class WarehouseUpdate(BaseUpdateSchema):
    """Schema for updating a warehouse"""

    name: str | None = Field(None, min_length=1, max_length=255)
    code: str | None = Field(None, max_length=32)
    address: str | None = None
    city: str | None = Field(None, max_length=100)
    region: str | None = Field(None, max_length=100)
    postal_code: str | None = Field(None, max_length=20)
    phone: str | None = Field(None, max_length=32)
    email: EmailStr | None = None
    manager_name: str | None = Field(None, max_length=255)
    working_hours: dict | None = None
    is_active: bool | None = None
    is_main: bool | None = None


class WarehouseResponse(BaseResponseSchema):
    """Schema for warehouse response"""

    name: str
    code: str | None
    address: str | None
    city: str | None
    region: str | None
    postal_code: str | None
    phone: str | None
    email: str | None
    manager_name: str | None
    working_hours: dict | None
    is_active: bool
    is_main: bool
    company_id: int


class ProductStockCreate(BaseCreateSchema):
    """Schema for creating product stock"""

    product_id: int
    warehouse_id: int
    quantity: int = Field(..., ge=0)
    min_quantity: int = Field(default=0, ge=0)
    max_quantity: int | None = Field(None, gt=0)
    location: str | None = Field(None, max_length=100)


class ProductStockUpdate(BaseUpdateSchema):
    """Schema for updating product stock"""

    quantity: int | None = Field(None, ge=0)
    min_quantity: int | None = Field(None, ge=0)
    max_quantity: int | None = Field(None, gt=0)
    location: str | None = Field(None, max_length=100)


class ProductStockResponse(BaseResponseSchema):
    """Schema for product stock response"""

    product_id: int
    warehouse_id: int
    quantity: int
    reserved_quantity: int
    min_quantity: int
    max_quantity: int | None
    location: str | None
    available_quantity: int
    is_low_stock: bool
    product_sku: str | None
    product_name: str | None
    warehouse_name: str | None


class StockMovementCreate(BaseCreateSchema):
    """Schema for creating stock movement"""

    stock_id: int
    movement_type: str = Field(..., max_length=32)  # in, out, transfer, adjustment
    quantity: int  # Positive for in, negative for out
    reference_type: str | None = Field(None, max_length=32)
    reference_id: int | None = None
    reason: str | None = Field(None, max_length=255)
    notes: str | None = None


class StockMovementResponse(BaseResponseSchema):
    """Schema for stock movement response"""

    stock_id: int
    movement_type: str
    quantity: int
    previous_quantity: int
    new_quantity: int
    reference_type: str | None
    reference_id: int | None
    reason: str | None
    notes: str | None
    user_id: int | None
    product_sku: str | None
    product_name: str | None
    warehouse_name: str | None


class StockTransfer(BaseCreateSchema):
    """Schema for stock transfer between warehouses"""

    product_id: int
    from_warehouse_id: int
    to_warehouse_id: int
    quantity: int = Field(..., gt=0)
    reason: str | None = Field(None, max_length=255)
    notes: str | None = None


class StockAdjustment(BaseCreateSchema):
    """Schema for stock adjustment"""

    adjustments: list[dict] = Field(..., min_items=1)  # List of {product_id, warehouse_id, new_quantity, reason}
    notes: str | None = None


class WarehouseStats(BaseCreateSchema):
    """Schema for warehouse statistics"""

    total_products: int
    total_stock: int
    low_stock_products: int
    out_of_stock_products: int
    total_value: float
