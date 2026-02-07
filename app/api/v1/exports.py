from __future__ import annotations

from datetime import UTC, datetime
from io import BytesIO
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from openpyxl import Workbook
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_async_db
from app.core.dependencies import require_store_admin
from app.core.security import resolve_tenant_company_id
from app.models.order import Order, OrderItem
from app.utils.pii import mask_phone

router = APIRouter(prefix="/exports", tags=["exports"])


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


async def _fetch_orders(
	db: AsyncSession,
	*,
	company_id: int,
	date_from: datetime | None,
	date_to: datetime | None,
	limit: int,
) -> list[dict[str, Any]]:
	items_count = func.count(OrderItem.id).label("items_count")
	stmt = select(Order, items_count).outerjoin(OrderItem).where(Order.company_id == company_id)
	if date_from:
		stmt = stmt.where(Order.created_at >= date_from)
	if date_to:
		stmt = stmt.where(Order.created_at <= date_to)
	stmt = stmt.group_by(Order.id).order_by(Order.created_at.desc()).limit(limit)
	rows = (await db.execute(stmt)).all()

	results: list[dict[str, Any]] = []
	for order, count in rows:
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


@router.get("/orders.xlsx")
async def export_orders_xlsx(
	date_from: str | None = Query(default=None),
	date_to: str | None = Query(default=None),
	limit: int = Query(default=1000, ge=1, le=5000),
	admin=Depends(require_store_admin),
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
