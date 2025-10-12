# app/routers/analytics.py
from __future__ import annotations

"""
Analytics router for business intelligence and reporting.
"""

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends
from sqlalchemy import and_, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

# DB dependency (db / database fallback)
try:
    from app.core.database import get_db  # type: ignore
except Exception:
    from app.core.db import get_db  # type: ignore

from app.core.deps import api_rate_limit_dep, ensure_idempotency
from app.core.errors import bad_request, server_error
from app.core.security import require_analyst
from app.models import Order, OrderItem, Product, User
from app.schemas import (
    AnalyticsFilter,
    CustomerAnalytics,
    DashboardStats,
    ExportRequest,
    ProductAnalytics,
    SalesAnalytics,
)
from app.utils.excel import export_analytics_to_excel
from app.utils.pdf import export_analytics_to_pdf

router = APIRouter(prefix="/analytics", tags=["analytics"])


# ---------- helpers ----------


def _parse_dt_or_default(value: Optional[str], default: datetime) -> datetime:
    if not value:
        return default
    # допускаем ISO-строки без таймзоны
    try:
        return datetime.fromisoformat(value)
    except Exception:
        # мягкая деградация — если пришёл мусор, вернём дефолт
        return default


def _float_safe(x: Optional[Decimal | float | int]) -> float:
    if x is None:
        return 0.0
    if isinstance(x, Decimal):
        return float(x)
    return float(x)


def _normalize_interval(interval: Optional[str]) -> str:
    if not interval:
        return "day"
    val = interval.lower()
    if val in {"day", "week", "month"}:
        return val
    return "day"


# ---------- endpoints ----------


@router.get(
    "/dashboard",
    response_model=DashboardStats,
    dependencies=[Depends(api_rate_limit_dep)],
)
async def get_dashboard_stats(
    current_user: User = Depends(require_analyst),
    db: AsyncSession = Depends(get_db),
):
    """Get dashboard statistics for current company."""

    # total orders
    total_orders = (
        await db.execute(
            select(func.count(Order.id)).where(
                and_(Order.company_id == current_user.company_id, Order.is_deleted.is_(False))
            )
        )
    ).scalar() or 0

    # total revenue (completed/paid)
    total_revenue_dec = (
        await db.execute(
            select(func.coalesce(func.sum(Order.total_amount), 0)).where(
                and_(
                    Order.company_id == current_user.company_id,
                    Order.status.in_(["completed", "paid"]),
                    Order.is_deleted.is_(False),
                )
            )
        )
    ).scalar() or Decimal(0)
    total_revenue = _float_safe(total_revenue_dec)

    # total products
    total_products = (
        await db.execute(
            select(func.count(Product.id)).where(
                and_(Product.company_id == current_user.company_id, Product.is_deleted.is_(False))
            )
        )
    ).scalar() or 0

    # unique customers
    total_customers = (
        await db.execute(
            select(func.count(func.distinct(Order.customer_phone))).where(
                and_(
                    Order.company_id == current_user.company_id,
                    Order.customer_phone.isnot(None),
                    Order.is_deleted.is_(False),
                )
            )
        )
    ).scalar() or 0

    # pending orders
    pending_orders = (
        await db.execute(
            select(func.count(Order.id)).where(
                and_(
                    Order.company_id == current_user.company_id,
                    Order.status == "pending",
                    Order.is_deleted.is_(False),
                )
            )
        )
    ).scalar() or 0

    # recent orders
    recent_orders_res = await db.execute(
        select(Order)
        .where(and_(Order.company_id == current_user.company_id, Order.is_deleted.is_(False)))
        .order_by(desc(Order.created_at))
        .limit(5)
    )
    recent_orders_rows = recent_orders_res.scalars().all()
    recent_orders = [
        {
            "id": r.id,
            "order_number": r.order_number,
            "customer_name": r.customer_name,
            "total_amount": _float_safe(r.total_amount),
            "status": r.status,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in recent_orders_rows
    ]

    # sales chart (last 7 days)
    end_date = datetime.utcnow()
    start_date = end_date - timedelta(days=6)
    sales_data = await get_sales_data(
        db=db,
        company_id=current_user.company_id,
        start_date=start_date,
        end_date=end_date,
        interval="day",
    )

    # top products
    top_q = await db.execute(
        select(
            Product.name,
            func.sum(OrderItem.quantity).label("total_sold"),
            func.sum(OrderItem.total_price).label("total_revenue"),
        )
        .join(OrderItem, OrderItem.product_id == Product.id)
        .join(Order, Order.id == OrderItem.order_id)
        .where(
            and_(
                Order.company_id == current_user.company_id,
                Order.status.in_(["completed", "paid"]),
                Order.is_deleted.is_(False),
            )
        )
        .group_by(Product.id, Product.name)
        .order_by(desc("total_revenue"))
        .limit(5)
    )
    top_products = [
        {
            "name": row.name,
            "total_sold": int(row.total_sold or 0),
            "total_revenue": _float_safe(row.total_revenue),
        }
        for row in top_q
    ]

    # low_stock_alerts — можно внедрить позже из таблиц склада
    return DashboardStats(
        total_orders=total_orders,
        total_revenue=total_revenue,
        total_products=total_products,
        total_customers=total_customers,
        pending_orders=pending_orders,
        low_stock_alerts=0,
        recent_orders=recent_orders,
        sales_chart=sales_data,
        top_products=top_products,
    )


@router.get(
    "/sales",
    response_model=SalesAnalytics,
    dependencies=[Depends(api_rate_limit_dep)],
)
async def get_sales_analytics(
    filter_params: AnalyticsFilter = Depends(),
    current_user: User = Depends(require_analyst),
    db: AsyncSession = Depends(get_db),
):
    """Get sales analytics data in a given interval."""
    interval = _normalize_interval(filter_params.interval)

    # dates
    end_dt_default = datetime.utcnow()
    start_dt_default = end_dt_default - timedelta(days=30)
    end_date = _parse_dt_or_default(filter_params.date_to, end_dt_default)
    start_date = _parse_dt_or_default(filter_params.date_from, start_dt_default)

    if start_date > end_date:
        raise bad_request("date_from must be less than or equal to date_to")

    return await get_sales_data(
        db=db,
        company_id=current_user.company_id,
        start_date=start_date,
        end_date=end_date,
        interval=interval,
    )


@router.get(
    "/customers",
    response_model=CustomerAnalytics,
    dependencies=[Depends(api_rate_limit_dep)],
)
async def get_customer_analytics(
    filter_params: AnalyticsFilter = Depends(),
    current_user: User = Depends(require_analyst),
    db: AsyncSession = Depends(get_db),
):
    """Get customer analytics data."""
    end_dt_default = datetime.utcnow()
    start_dt_default = end_dt_default - timedelta(days=30)
    end_date = _parse_dt_or_default(filter_params.date_to, end_dt_default)
    start_date = _parse_dt_or_default(filter_params.date_from, start_dt_default)

    if start_date > end_date:
        raise bad_request("date_from must be less than or equal to date_to")

    # total unique customers
    total_customers = (
        await db.execute(
            select(func.count(func.distinct(Order.customer_phone))).where(
                and_(
                    Order.company_id == current_user.company_id,
                    Order.customer_phone.isnot(None),
                    Order.created_at >= start_date,
                    Order.created_at <= end_date,
                    Order.is_deleted.is_(False),
                )
            )
        )
    ).scalar() or 0

    # customers by order count
    by_count_res = await db.execute(
        select(Order.customer_phone, func.count(Order.id).label("order_count"))
        .where(
            and_(
                Order.company_id == current_user.company_id,
                Order.customer_phone.isnot(None),
                Order.created_at >= start_date,
                Order.created_at <= end_date,
                Order.is_deleted.is_(False),
            )
        )
        .group_by(Order.customer_phone)
    )
    customer_data = by_count_res.all()

    new_customers = len([c for c in customer_data if int(c.order_count or 0) == 1])
    repeat_customers = len([c for c in customer_data if int(c.order_count or 0) > 1])
    repeat_rate = (repeat_customers / total_customers) if total_customers > 0 else 0.0

    total_orders = sum(int(c.order_count or 0) for c in customer_data)
    avg_orders_per_customer = (total_orders / total_customers) if total_customers > 0 else 0.0

    # top customers by spend
    top_res = await db.execute(
        select(
            Order.customer_phone,
            Order.customer_name,
            func.count(Order.id).label("order_count"),
            func.sum(Order.total_amount).label("total_spent"),
        )
        .where(
            and_(
                Order.company_id == current_user.company_id,
                Order.customer_phone.isnot(None),
                Order.status.in_(["completed", "paid"]),
                Order.created_at >= start_date,
                Order.created_at <= end_date,
                Order.is_deleted.is_(False),
            )
        )
        .group_by(Order.customer_phone, Order.customer_name)
        .order_by(desc("total_spent"))
        .limit(10)
    )
    top_customers = [
        {
            "phone": row.customer_phone,
            "name": row.customer_name,
            "order_count": int(row.order_count or 0),
            "total_spent": _float_safe(row.total_spent),
        }
        for row in top_res
    ]

    return CustomerAnalytics(
        total_customers=total_customers,
        new_customers=new_customers,
        repeat_customers=repeat_customers,
        repeat_rate=repeat_rate,
        average_orders_per_customer=avg_orders_per_customer,
        top_customers=top_customers,
    )


@router.get(
    "/products",
    response_model=ProductAnalytics,
    dependencies=[Depends(api_rate_limit_dep)],
)
async def get_product_analytics(
    filter_params: AnalyticsFilter = Depends(),
    current_user: User = Depends(require_analyst),
    db: AsyncSession = Depends(get_db),
):
    """Get product analytics data."""
    end_dt_default = datetime.utcnow()
    start_dt_default = end_dt_default - timedelta(days=30)
    end_date = _parse_dt_or_default(filter_params.date_to, end_dt_default)
    start_date = _parse_dt_or_default(filter_params.date_from, start_dt_default)

    if start_date > end_date:
        raise bad_request("date_from must be less than or equal to date_to")

    # top selling products
    top_res = await db.execute(
        select(
            Product.id,
            Product.name,
            Product.sku,
            func.sum(OrderItem.quantity).label("total_sold"),
            func.sum(OrderItem.total_price).label("total_revenue"),
        )
        .join(OrderItem, OrderItem.product_id == Product.id)
        .join(Order, Order.id == OrderItem.order_id)
        .where(
            and_(
                Order.company_id == current_user.company_id,
                Order.status.in_(["completed", "paid"]),
                Order.created_at >= start_date,
                Order.created_at <= end_date,
                Order.is_deleted.is_(False),
            )
        )
        .group_by(Product.id, Product.name, Product.sku)
        .order_by(desc("total_revenue"))
        .limit(20)
    )
    top_products = [
        {
            "id": row.id,
            "name": row.name,
            "sku": row.sku,
            "total_sold": int(row.total_sold or 0),
            "total_revenue": _float_safe(row.total_revenue),
        }
        for row in top_res
    ]

    # category performance
    cat_res = await db.execute(
        select(
            Product.category,
            func.sum(OrderItem.quantity).label("total_sold"),
            func.sum(OrderItem.total_price).label("total_revenue"),
        )
        .join(OrderItem, OrderItem.product_id == Product.id)
        .join(Order, Order.id == OrderItem.order_id)
        .where(
            and_(
                Order.company_id == current_user.company_id,
                Order.status.in_(["completed", "paid"]),
                Order.created_at >= start_date,
                Order.created_at <= end_date,
                Product.category.isnot(None),
                Order.is_deleted.is_(False),
            )
        )
        .group_by(Product.category)
        .order_by(desc("total_revenue"))
    )
    category_performance = [
        {
            "category": row.category,
            "total_sold": int(row.total_sold or 0),
            "total_revenue": _float_safe(row.total_revenue),
        }
        for row in cat_res
    ]

    # low/out of stock — можно подгрузить из склада позже
    return ProductAnalytics(
        top_products=top_products,
        low_stock_products=[],
        out_of_stock_products=[],
        category_performance=category_performance,
    )


@router.post(
    "/export",
    dependencies=[Depends(api_rate_limit_dep), Depends(ensure_idempotency)],
)
async def export_analytics(
    export_request: ExportRequest,
    current_user: User = Depends(require_analyst),
    db: AsyncSession = Depends(get_db),
):
    """
    Export analytics data to Excel or PDF.
    Идемпотентность включена через ensure_idempotency (Idempotency-Key).
    """
    try:
        fmt = (export_request.format or "").lower()
        if fmt == "xlsx":
            file_path = await export_analytics_to_excel(
                export_type=export_request.export_type,
                company_id=current_user.company_id,
                filters=export_request.filters or {},
                db=db,
            )
        elif fmt == "pdf":
            file_path = await export_analytics_to_pdf(
                export_type=export_request.export_type,
                company_id=current_user.company_id,
                filters=export_request.filters or {},
                db=db,
            )
        else:
            raise bad_request("Unsupported export format")

        return {"download_url": file_path}
    except Exception as e:
        raise server_error(f"Export failed: {e!s}")


# ---------- shared query ----------


async def get_sales_data(
    db: AsyncSession,
    company_id: int,
    start_date: datetime,
    end_date: datetime,
    interval: str,
) -> SalesAnalytics:
    """
    Aggregates sales data grouped by day/week/month for given date range.
    Returns labels/data arrays + total/average and growth_rate for last period.
    """
    interval = _normalize_interval(interval)

    # date_trunc by interval
    if interval == "day":
        date_trunc = func.date_trunc("day", Order.created_at)
    elif interval == "week":
        date_trunc = func.date_trunc("week", Order.created_at)
    else:  # month
        date_trunc = func.date_trunc("month", Order.created_at)

    res = await db.execute(
        select(
            date_trunc.label("period"),
            func.coalesce(func.sum(Order.total_amount), 0).label("revenue"),
        )
        .where(
            and_(
                Order.company_id == company_id,
                Order.status.in_(["completed", "paid"]),
                Order.created_at >= start_date,
                Order.created_at <= end_date,
                Order.is_deleted.is_(False),
            )
        )
        .group_by("period")
        .order_by("period")
    )
    rows = res.all()

    labels: list[str] = []
    data: list[float] = []
    for row in rows:
        period: datetime = row.period  # date_trunc returns timestamp
        if interval == "day":
            labels.append(period.strftime("%Y-%m-%d"))
        elif interval == "week":
            labels.append(f"Week of {period.strftime('%Y-%m-%d')}")
        else:
            labels.append(period.strftime("%Y-%m"))
        data.append(_float_safe(row.revenue))

    total = _float_safe(sum(data))
    average = total / len(data) if data else 0.0

    growth_rate: Optional[float] = None
    if len(data) >= 2:
        prev = data[-2]
        cur = data[-1]
        if prev > 0:
            growth_rate = (cur - prev) / prev * 100.0

    return SalesAnalytics(
        labels=labels,
        data=data,
        total=total,
        average=average,
        growth_rate=growth_rate,
    )
