from __future__ import annotations

SEVERITY_SEV1 = "SEV-1"
SEVERITY_SEV2 = "SEV-2"
SEVERITY_SEV3 = "SEV-3"
SEVERITY_SEV4 = "SEV-4"

AREA_AUTH = "auth"
AREA_BILLING = "billing"
AREA_KASPI = "kaspi"
AREA_REPRICING = "repricing"
AREA_PREORDER = "preorder"
AREA_REPORTS = "reports"
AREA_INTEGRATIONS = "integrations"
AREA_PLATFORM = "platform"

ALLOWED_SEVERITIES = {
    SEVERITY_SEV1,
    SEVERITY_SEV2,
    SEVERITY_SEV3,
    SEVERITY_SEV4,
}

ALLOWED_AREAS = {
    AREA_AUTH,
    AREA_BILLING,
    AREA_KASPI,
    AREA_REPRICING,
    AREA_PREORDER,
    AREA_REPORTS,
    AREA_INTEGRATIONS,
    AREA_PLATFORM,
}


def validate_severity(value: str) -> str:
    normalized = (value or "").strip().upper()
    if normalized not in ALLOWED_SEVERITIES:
        raise ValueError("invalid_severity")
    return normalized


def validate_area(value: str) -> str:
    normalized = (value or "").strip().lower()
    if normalized not in ALLOWED_AREAS:
        raise ValueError("invalid_area")
    return normalized


def build_support_triage_preview(
    *,
    company_id: int,
    severity: str,
    area: str,
    issue_summary: str,
    latest_request_id: str | None = None,
) -> dict:
    normalized_severity = validate_severity(severity)
    normalized_area = validate_area(area)
    summary = (issue_summary or "").strip()
    if not summary:
        raise ValueError("empty_issue_summary")

    next_steps = [
        "confirm_tenant",
        "classify_area",
        "fetch_diagnostics",
        "determine_impact",
        "choose_next_action",
        "collect_evidence",
        "mark_status",
    ]

    required_inputs = [
        "company_id",
        "issue_summary",
        "severity",
        "first_observed_at",
    ]
    if latest_request_id:
        required_inputs.append("latest_request_id")

    return {
        "company_id": company_id,
        "severity": normalized_severity,
        "area": normalized_area,
        "issue_summary": summary,
        "normalized": True,
        "required_inputs": required_inputs,
        "recommended_next_steps": next_steps,
        "status": "preview",
        "automation_supported": False,
    }


__all__ = [
    "SEVERITY_SEV1",
    "SEVERITY_SEV2",
    "SEVERITY_SEV3",
    "SEVERITY_SEV4",
    "AREA_AUTH",
    "AREA_BILLING",
    "AREA_KASPI",
    "AREA_REPRICING",
    "AREA_PREORDER",
    "AREA_REPORTS",
    "AREA_INTEGRATIONS",
    "AREA_PLATFORM",
    "ALLOWED_SEVERITIES",
    "ALLOWED_AREAS",
    "validate_severity",
    "validate_area",
    "build_support_triage_preview",
]
