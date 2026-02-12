from __future__ import annotations

from datetime import UTC, date, datetime, time
from io import BytesIO
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.responses import StreamingResponse
from openpyxl import Workbook
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_async_db
from app.core.dependencies import (
    get_current_verified_user,
    require_active_subscription,
    require_company_access,
    require_store_admin_company,
)
from app.core.security import resolve_tenant_company_id
from app.models.billing import BillingInvoice
from app.models.order import Order, OrderItem
from app.models.product import Product
from app.models.user import User
from app.services.exports.products_xlsx import build_products_xlsx
from app.services.exports.sales_xlsx import build_sales_xlsx
from app.utils.pii import mask_phone


async def _require_company_context(current_user: User = Depends(get_current_verified_user)) -> User:
    resolve_tenant_company_id(current_user, not_found_detail="Company not set")
    return current_user


router = APIRouter(
    prefix="/exports",
    tags=["exports"],
    dependencies=[
        Depends(require_company_access),
        Depends(_require_company_context),
        Depends(require_store_admin_company),
        Depends(require_active_subscription),
    ],
)


def _parse_dt(value: str | None, field: str) -> datetime | None:
    if not value:
        return None
    v = value.strip()
    if v.endswith("Z"):
        v = v[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(v)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"{field} must be ISO 8601") from exc
    if dt.tzinfo is not None:
        dt = dt.astimezone(UTC).replace(tzinfo=None)
    return dt


def _parse_date(value: str | None, field: str) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value.strip())
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"{field} must be YYYY-MM-DD") from exc


def _date_bounds(date_from: date | None, date_to: date | None) -> tuple[datetime | None, datetime | None]:
    start_dt = datetime.combine(date_from, time.min) if date_from else None
    end_dt = datetime.combine(date_to, time.max) if date_to else None
    return start_dt, end_dt


async def _fetch_orders(
    db: AsyncSession,
    *,
    company_id: int,
    date_from: datetime | None,
    date_to: datetime | None,
    limit: int,
) -> list[dict[str, Any]]:
    items_count_sq = (
        select(
            OrderItem.order_id.label("oid"),
            func.count(OrderItem.id).label("items_count"),
        )
        .group_by(OrderItem.order_id)
        .subquery()
    )
    items_count = func.coalesce(items_count_sq.c.items_count, 0).label("items_count")

    last_invoice_id_sq = (
        select(func.max(BillingInvoice.id))
        .where(BillingInvoice.order_id == Order.id)
        .correlate(Order)
        .scalar_subquery()
    )

    stmt = (
        select(Order, items_count, BillingInvoice)
        .outerjoin(items_count_sq, items_count_sq.c.oid == Order.id)
        .outerjoin(BillingInvoice, BillingInvoice.id == last_invoice_id_sq)
        .where(Order.company_id == company_id)
    )
    if date_from:
        stmt = stmt.where(Order.created_at >= date_from)
    if date_to:
        stmt = stmt.where(Order.created_at <= date_to)
    stmt = stmt.order_by(Order.created_at.desc()).limit(limit)
    rows = (await db.execute(stmt)).all()

    results: list[dict[str, Any]] = []
    for order, count, _invoice in rows:
        results.append(
            {
                "order_id": order.id,
                "created_at": order.created_at.isoformat() if order.created_at else None,
                "status": str(order.status),
                "total_price": str(order.total_amount) if order.total_amount is not None else None,
                "customer_name": order.customer_name or "",
                "customer_phone": mask_phone(order.customer_phone or "") if order.customer_phone else "",
                "items_count": int(count or 0),
            }
        )
    return results


async def _fetch_sales_rows(
    db: AsyncSession,
    *,
    company_id: int,
    date_from: datetime | None,
    date_to: datetime | None,
    limit: int,
) -> list[dict[str, Any]]:
    items_count_sq = (
        select(
            OrderItem.order_id.label("oid"),
            func.count(OrderItem.id).label("items_count"),
        )
        .group_by(OrderItem.order_id)
        .subquery()
    )
    items_count = func.coalesce(items_count_sq.c.items_count, 0).label("items_count")

    stmt = (
        select(Order, items_count)
        .outerjoin(items_count_sq, items_count_sq.c.oid == Order.id)
        .where(Order.company_id == company_id)
    )
    if date_from:
        stmt = stmt.where(Order.created_at >= date_from)
    if date_to:
        stmt = stmt.where(Order.created_at <= date_to)
    stmt = stmt.order_by(Order.created_at.desc()).limit(limit)
    rows = (await db.execute(stmt)).all()

    results: list[dict[str, Any]] = []
    for order, count in rows:
        results.append(
            {
                "order_id": order.id,
                "created_at": order.created_at.isoformat() if order.created_at else None,
                "total_amount": str(order.total_amount) if order.total_amount is not None else None,
                "items_count": int(count or 0),
            }
        )
    return results


async def _fetch_products_rows(
    db: AsyncSession,
    *,
    company_id: int,
    limit: int,
) -> list[dict[str, Any]]:
    stmt = (
        select(Product.id, Product.sku, Product.name, Product.price, Product.created_at)
        .where(Product.company_id == company_id)
        .order_by(Product.created_at.desc())
        .limit(limit)
    )
    rows = (await db.execute(stmt)).all()

    results: list[dict[str, Any]] = []
    for pid, sku, name, price, created_at in rows:
        results.append(
            {
                "product_id": pid,
                "sku": sku or "",
                "name": name or "",
                "price": str(price) if price is not None else "",
                "created_at": created_at.isoformat() if created_at else "",
            }
        )
    return results


@router.get("/orders.xlsx")
async def export_orders_xlsx(
    date_from: str | None = Query(default=None),
    date_to: str | None = Query(default=None),
    limit: int = Query(default=1000, ge=1, le=5000),
    admin: User = Depends(get_current_verified_user),
    db: AsyncSession = Depends(get_async_db),
) -> Response:
    _ = admin
    company_id = resolve_tenant_company_id(admin, not_found_detail="Company not set")
    df = _parse_dt(date_from, "date_from")
    dt = _parse_dt(date_to, "date_to")

    rows = await _fetch_orders(db, company_id=company_id, date_from=df, date_to=dt, limit=limit)

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "orders"

    headers = [
        "order_id",
        "created_at",
        "status",
        "total_price",
        "customer_name",
        "customer_phone",
        "items_count",
    ]
    sheet.append(headers)
    for row in rows:
        sheet.append([row.get(h) for h in headers])

    buffer = BytesIO()
    workbook.save(buffer)
    buffer.seek(0)

    return Response(
        content=buffer.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=orders.xlsx"},
    )


@router.get("/sales.xlsx")
async def export_sales_xlsx(
    date_from: str | None = Query(default=None),
    date_to: str | None = Query(default=None),
    limit: int = Query(default=1000, ge=1, le=5000),
    admin: User = Depends(get_current_verified_user),
    db: AsyncSession = Depends(get_async_db),
) -> StreamingResponse:
    _ = admin
    company_id = resolve_tenant_company_id(admin, not_found_detail="Company not set")
    start_dt, end_dt = _date_bounds(_parse_date(date_from, "date_from"), _parse_date(date_to, "date_to"))

    rows = await _fetch_sales_rows(db, company_id=company_id, date_from=start_dt, date_to=end_dt, limit=limit)
    content = build_sales_xlsx(rows)

    return StreamingResponse(
        BytesIO(content),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=sales.xlsx"},
    )


@router.get("/products.xlsx")
async def export_products_xlsx(
    limit: int = Query(default=1000, ge=1, le=5000),
    admin: User = Depends(get_current_verified_user),
    db: AsyncSession = Depends(get_async_db),
) -> StreamingResponse:
    _ = admin
    company_id = resolve_tenant_company_id(admin, not_found_detail="Company not set")

    rows = await _fetch_products_rows(db, company_id=company_id, limit=limit)
    content = build_products_xlsx(rows)

    return StreamingResponse(
        BytesIO(content),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=products.xlsx"},
    )
