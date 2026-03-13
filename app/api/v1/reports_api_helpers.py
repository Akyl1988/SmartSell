from __future__ import annotations

import csv
import json
from datetime import UTC, date, datetime, time
from io import StringIO
from typing import Any

from fastapi import HTTPException, Request
from fastapi.responses import StreamingResponse

from app.core.exceptions import AuthorizationError, NotFoundError, _ensure_request_id
from app.core.logging import audit_logger
from app.core.rbac import is_platform_admin, is_store_admin, is_store_manager
from app.core.security import resolve_tenant_company_id
from app.models.user import User


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


def _resolve_wallet_report_company_id(
    current_user: User,
    company_id: int | None,
) -> int:
    if is_platform_admin(current_user):
        if company_id is not None:
            return int(company_id)
        return resolve_tenant_company_id(current_user, not_found_detail="Company not set")
    if not (is_store_admin(current_user) or is_store_manager(current_user)):
        raise AuthorizationError("Admin role required", "ADMIN_REQUIRED")
    resolved_company_id = resolve_tenant_company_id(current_user, not_found_detail="Company not set")
    if company_id is not None and int(company_id) != int(resolved_company_id):
        raise NotFoundError("company_not_found", code="company_not_found", http_status=404)
    return int(resolved_company_id)


def _safe_reference(reference_type: str | None, reference_id: int | None) -> str:
    ref = (reference_type or "").strip()
    if not ref:
        return ""
    if reference_id is None:
        return ref
    return f"{ref}:{reference_id}"


def _extract_kaspi_attrs(internal_notes: Any) -> dict[str, Any]:
    if internal_notes is None:
        return {}
    if isinstance(internal_notes, dict):
        data = internal_notes
    elif isinstance(internal_notes, str):
        try:
            data = json.loads(internal_notes) if internal_notes.strip() else {}
        except json.JSONDecodeError:
            return {}
    else:
        return {}
    if not isinstance(data, dict):
        return {}
    kaspi = data.get("kaspi")
    return kaspi if isinstance(kaspi, dict) else {}


def _to_optional_str(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return str(value)


def _resolve_company_id_param(request: Request, company_id: int | None) -> int | None:
    if company_id is not None:
        return int(company_id)
    raw = request.query_params.get("company_id")
    if raw is None:
        return None
    try:
        return int(raw)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="company_id must be integer") from exc


def _csv_stream(rows: list[dict[str, str]], headers: list[str]) -> Any:
    buffer = StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(headers)
    yield buffer.getvalue().encode("utf-8")
    buffer.seek(0)
    buffer.truncate(0)
    for row in rows:
        writer.writerow([row.get(col, "") for col in headers])
        yield buffer.getvalue().encode("utf-8")
        buffer.seek(0)
        buffer.truncate(0)


def build_csv_streaming_response(
    *,
    rows: list[dict[str, str]],
    headers: list[str],
    filename: str,
) -> StreamingResponse:
    return StreamingResponse(
        _csv_stream(rows, headers),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


def _log_report_event(
    *,
    request: Request,
    event: str,
    resolved_company_id: int,
    date_from: str | None,
    date_to: str | None,
    limit: int,
    rows_count: int,
) -> None:
    request_id = _ensure_request_id(request)
    audit_logger.log_system_event(
        level="info",
        event=event,
        message="CSV report generated",
        meta={
            "request_id": request_id,
            "company_id": resolved_company_id,
            "date_from": date_from,
            "date_to": date_to,
            "limit": limit,
            "rows_count": rows_count,
        },
    )


def _log_report_pdf_event(
    *,
    request: Request,
    event: str,
    resolved_company_id: int,
    date_from: str | None,
    date_to: str | None,
    limit: int,
    rows_count: int | None = None,
    extra_meta: dict[str, Any] | None = None,
) -> None:
    request_id = _ensure_request_id(request)
    meta: dict[str, Any] = {
        "request_id": request_id,
        "company_id": resolved_company_id,
        "date_from": date_from,
        "date_to": date_to,
        "limit": limit,
    }
    if rows_count is not None:
        meta["rows_count"] = rows_count
    if extra_meta:
        meta.update(extra_meta)
    audit_logger.log_system_event(
        level="info",
        event=event,
        message="PDF report generated",
        meta=meta,
    )
