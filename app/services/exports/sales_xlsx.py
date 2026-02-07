from __future__ import annotations

from io import BytesIO
from typing import Any

from openpyxl import Workbook


def build_sales_xlsx(rows: list[dict[str, Any]]) -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "sales"

    headers = ["order_id", "created_at", "total_amount", "items_count"]
    sheet.append(headers)
    for row in rows:
        sheet.append([row.get(h) for h in headers])

    buffer = BytesIO()
    workbook.save(buffer)
    buffer.seek(0)
    return buffer.getvalue()
