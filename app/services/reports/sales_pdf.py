from __future__ import annotations

from io import BytesIO
from typing import Any

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle


def build_sales_pdf(
    *,
    metrics: dict[str, Any],
    top_skus: list[dict[str, Any]],
    date_from: str | None,
    date_to: str | None,
) -> bytes:
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=1 * cm, leftMargin=1 * cm, pageCompression=0)
    styles = getSampleStyleSheet()
    elements: list[Any] = []

    title = "Sales Analytics"
    elements.append(Paragraph(title, styles["Title"]))
    range_text = f"Date range: {date_from or '-'} .. {date_to or '-'}"
    elements.append(Paragraph(range_text, styles["Normal"]))

    elements.append(Paragraph(f"Total orders: {metrics.get('total_orders', 0)}", styles["Normal"]))
    total_revenue = metrics.get("total_revenue", 0)
    elements.append(Paragraph(f"Total revenue: {total_revenue}", styles["Normal"]))
    avg_order_value = metrics.get("avg_order_value", 0)
    elements.append(Paragraph(f"Avg order value: {avg_order_value}", styles["Normal"]))
    elements.append(Paragraph(f"Items sold total: {metrics.get('items_sold_total', 0)}", styles["Normal"]))
    elements.append(Spacer(1, 12))

    table_data = [["sku", "qty"]]
    for row in top_skus:
        table_data.append([row.get("sku", ""), row.get("qty", 0)])

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
    return buffer.getvalue()
