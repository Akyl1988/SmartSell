from __future__ import annotations

from datetime import datetime, timedelta

_TERMINAL_STATUSES = {
    "FINISHED",
    "FINISHED_OK",
    "FINISHED_ERROR",
    "FAILED",
    "ERROR",
    "ABORTED",
    "DONE",
    "COMPLETED",
    "DUPLICATE",
}
_FAILED_STATUSES = {"FAILED", "ERROR", "ABORTED", "FINISHED_ERROR"}
_SUCCESS_STATUSES = {"FINISHED_OK", "DONE", "COMPLETED", "SUCCESS"}


def normalize_import_status(status: str | None) -> str:
    return str(status or "").strip().upper()


def is_terminal_import_status(status: str | None) -> bool:
    return normalize_import_status(status) in _TERMINAL_STATUSES


def is_success_import_status(status: str | None) -> bool:
    return normalize_import_status(status) in _SUCCESS_STATUSES


def is_failed_import_status(status: str | None) -> bool:
    return normalize_import_status(status) in _FAILED_STATUSES


def extract_result_summary(payload: dict | None) -> dict[str, int]:
    data = payload or {}
    errors = int(data.get("errors") or 0)
    warnings = int(data.get("warnings") or 0)
    skipped = int(data.get("skipped") or 0)
    total = int(data.get("total") or 0)
    return {
        "errors": errors,
        "warnings": warnings,
        "skipped": skipped,
        "total": total,
    }


def classify_import_result(status: str | None, result_payload: dict | None) -> tuple[str, dict[str, int]]:
    normalized = normalize_import_status(status)
    summary = extract_result_summary(result_payload)

    if normalized in {"UPLOADED", "PROCESSING"}:
        return "pending", summary

    if is_failed_import_status(normalized):
        return "failed", summary

    if normalized == "FINISHED":
        if not result_payload:
            return "pending", summary
        if summary["errors"] > 0:
            return "failed", summary
        if summary["total"] > 0:
            return "success_applied", summary
        return "success_noop", summary

    if is_success_import_status(normalized):
        if summary["errors"] > 0:
            return "failed", summary
        if summary["total"] > 0:
            return "success_applied", summary
        if result_payload:
            return "success_noop", summary
        return "pending", summary

    if is_terminal_import_status(normalized):
        if summary["errors"] > 0:
            return "failed", summary
        if summary["total"] > 0:
            return "success_applied", summary
        if result_payload:
            return "success_noop", summary
        return "pending", summary

    return "pending", summary


def compute_backoff_seconds(*, attempts: int, base_delay_seconds: int, max_delay_seconds: int) -> int:
    safe_attempts = max(1, int(attempts) if attempts is not None else 1)
    base = max(1, int(base_delay_seconds))
    max_delay = max(base, int(max_delay_seconds))
    delay = base * (2 ** (safe_attempts - 1))
    return min(delay, max_delay)


def compute_next_poll_at(
    *,
    now: datetime,
    status: str | None,
    attempts: int,
    base_delay_seconds: int,
    max_delay_seconds: int,
    result_payload: dict | None = None,
) -> datetime | None:
    normalized = normalize_import_status(status)
    if normalized == "FINISHED" and not result_payload:
        normalized = "PENDING"
    if is_terminal_import_status(normalized):
        return None
    delay_seconds = compute_backoff_seconds(
        attempts=attempts,
        base_delay_seconds=base_delay_seconds,
        max_delay_seconds=max_delay_seconds,
    )
    return now + timedelta(seconds=delay_seconds)
