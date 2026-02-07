from __future__ import annotations

from io import BytesIO
from typing import Any

from openpyxl import Workbook


def build_products_xlsx(rows: list[dict[str, Any]]) -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "products"

    headers = ["product_id", "sku", "name", "price", "created_at"]
    sheet.append(headers)
    for row in rows:
        sheet.append([row.get(h) for h in headers])

    buffer = BytesIO()
    workbook.save(buffer)
    buffer.seek(0)
    return buffer.getvalue()
