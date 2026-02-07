from __future__ import annotations

from datetime import UTC, datetime
from io import BytesIO
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_async_db
from app.core.dependencies import require_store_admin
from app.core.security import resolve_tenant_company_id
from app.models.order import Order, OrderItem
from app.utils.pii import mask_phone

router = APIRouter(prefix="/reports", tags=["reports"])


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
				"order_id": str(order.id),
				"created_at": order.created_at.isoformat() if order.created_at else "",
				"status": str(order.status),
				"total_price": str(order.total_amount) if order.total_amount is not None else "",
				"customer_name": order.customer_name or "",
				"customer_phone": mask_phone(order.customer_phone or "") if order.customer_phone else "",
				"items_count": str(int(count or 0)),
			}
		)
	return results


@router.get("/orders.pdf")
async def report_orders_pdf(
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

	total_orders = len(rows)
	total_sum = sum(float(r.get("total_price") or 0) for r in rows)

	buffer = BytesIO()
	doc = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=1 * cm, leftMargin=1 * cm)
	styles = getSampleStyleSheet()
	elements: list[Any] = []

	title = "Orders Report"
	elements.append(Paragraph(title, styles["Title"]))
	range_text = f"Date range: {date_from or '-'} .. {date_to or '-'}"
	elements.append(Paragraph(range_text, styles["Normal"]))
	elements.append(Paragraph(f"Total orders: {total_orders}", styles["Normal"]))
	elements.append(Paragraph(f"Total amount: {total_sum:.2f}", styles["Normal"]))
	elements.append(Spacer(1, 12))

	table_data = [
		[
			"order_id",
			"created_at",
			"status",
			"total_price",
			"customer_name",
			"customer_phone",
			"items_count",
		]
	]
	for row in rows:
		table_data.append(
			[
				row["order_id"],
				row["created_at"],
				row["status"],
				row["total_price"],
				row["customer_name"],
				row["customer_phone"],
				row["items_count"],
			]
		)

	table = Table(table_data, repeatRows=1)
	table.setStyle(
		TableStyle(
			[
				("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
				("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
				("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
				("FONTSIZE", (0, 0), (-1, -1), 8),
			]
		)
	)
	elements.append(table)

	doc.build(elements)
	pdf_bytes = buffer.getvalue()

	return Response(
		content=pdf_bytes,
		media_type="application/pdf",
		headers={"Content-Disposition": "attachment; filename=orders.pdf"},
	)
