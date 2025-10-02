"""
Analytics Pydantic schemas.
"""

from decimal import Decimal
from typing import Any, Optional

from pydantic import Field

from app.schemas.base import BaseCreateSchema


class AnalyticsFilter(BaseCreateSchema):
    """Schema for analytics filtering"""

    date_from: Optional[str] = None
    date_to: Optional[str] = None
    interval: str = Field(default="day", regex="^(day|week|month)$")
    warehouse_id: Optional[int] = None
    category: Optional[str] = None
    product_id: Optional[int] = None


class SalesAnalytics(BaseCreateSchema):
    """Schema for sales analytics"""

    labels: list[str]
    data: list[Decimal]
    total: Decimal
    average: Decimal
    growth_rate: Optional[float] = None


class CustomerAnalytics(BaseCreateSchema):
    """Schema for customer analytics"""

    total_customers: int
    new_customers: int
    repeat_customers: int
    repeat_rate: float
    average_orders_per_customer: float
    top_customers: list[dict[str, Any]]


class ProductAnalytics(BaseCreateSchema):
    """Schema for product analytics"""

    top_products: list[dict[str, Any]]
    low_stock_products: list[dict[str, Any]]
    out_of_stock_products: list[dict[str, Any]]
    category_performance: list[dict[str, Any]]


class OrderAnalytics(BaseCreateSchema):
    """Schema for order analytics"""

    total_orders: int
    completed_orders: int
    cancelled_orders: int
    completion_rate: float
    average_order_value: Decimal
    order_trends: list[dict[str, Any]]


class WarehouseAnalytics(BaseCreateSchema):
    """Schema for warehouse analytics"""

    total_warehouses: int
    total_stock_value: Decimal
    stock_movements: list[dict[str, Any]]
    warehouse_performance: list[dict[str, Any]]


class FinancialAnalytics(BaseCreateSchema):
    """Schema for financial analytics"""

    total_revenue: Decimal
    net_profit: Decimal
    profit_margin: float
    revenue_trends: list[dict[str, Any]]
    payment_methods: list[dict[str, Any]]


class DashboardStats(BaseCreateSchema):
    """Schema for dashboard statistics"""

    total_orders: int
    total_revenue: Decimal
    total_products: int
    total_customers: int
    pending_orders: int
    low_stock_alerts: int
    recent_orders: list[dict[str, Any]]
    sales_chart: SalesAnalytics
    top_products: list[dict[str, Any]]


class ExportRequest(BaseCreateSchema):
    """Schema for export request"""

    export_type: str = Field(..., regex="^(sales|orders|products|customers|inventory)$")
    format: str = Field(default="xlsx", regex="^(xlsx|pdf|csv)$")
    date_from: Optional[str] = None
    date_to: Optional[str] = None
    filters: Optional[dict[str, Any]] = None
