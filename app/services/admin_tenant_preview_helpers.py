from __future__ import annotations

from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError, SmartSellValidationError
from app.core.support_workflow import build_support_triage_preview
from app.core.tenant_lifecycle import (
    TENANT_STATE_DELETE_REQUESTED,
    TENANT_STATE_PENDING_EXPORT,
    can_archive_tenant,
    can_request_delete,
    requires_export_before_delete,
)
from app.models.company import Company
from app.schemas.support_triage import SupportTriagePreviewIn, SupportTriagePreviewOut
from app.schemas.tenant_archive_delete import TenantArchiveDeletePreviewOut


async def load_company_or_404(db: AsyncSession, company_id: int) -> Company:
    company = await db.get(Company, company_id)
    if not company:
        raise NotFoundError("company_not_found", code="company_not_found", http_status=404)
    return company


def build_archive_delete_preview_payload(
    *,
    company_id: int,
    action: Literal["archive", "delete"],
    current_state: str,
) -> TenantArchiveDeletePreviewOut:
    if action == "archive":
        allowed = can_archive_tenant(current_state=current_state)
        next_state = "archived" if allowed else current_state
        required = ["platform_admin_reason", "evidence_trail"]
        warnings: list[str] = []
        if current_state == "archived":
            warnings.append("tenant_already_archived")

        return TenantArchiveDeletePreviewOut(
            company_id=company_id,
            current_state=current_state,
            requested_action=action,
            allowed=allowed,
            required_before_action=required,
            warnings=warnings,
            next_state=next_state,
            destructive_delete_supported=False,
        )

    export_required = requires_export_before_delete()
    has_export_manifest_reference = False
    allowed = can_request_delete(
        current_state=current_state,
        has_export_manifest_reference=has_export_manifest_reference,
    )
    warnings = ["delete_is_policy_only_no_destructive_delete"]
    required = ["platform_admin_reason", "evidence_trail"]
    if export_required:
        required.insert(0, "export_manifest_reference")
        if not has_export_manifest_reference:
            warnings.append("export_before_delete_required")

    if export_required and not has_export_manifest_reference:
        next_state = TENANT_STATE_PENDING_EXPORT
    elif allowed:
        next_state = TENANT_STATE_DELETE_REQUESTED
    else:
        next_state = current_state

    return TenantArchiveDeletePreviewOut(
        company_id=company_id,
        current_state=current_state,
        requested_action=action,
        allowed=allowed,
        required_before_action=required,
        warnings=warnings,
        next_state=next_state,
        destructive_delete_supported=False,
    )


def build_support_triage_preview_response(
    *,
    company_id: int,
    payload: SupportTriagePreviewIn,
) -> SupportTriagePreviewOut:
    try:
        preview = build_support_triage_preview(
            company_id=company_id,
            severity=payload.severity,
            area=payload.area,
            issue_summary=payload.issue_summary,
            latest_request_id=payload.latest_request_id,
        )
    except ValueError as exc:
        code = str(exc)
        raise SmartSellValidationError("Invalid support triage payload", code=code, http_status=422) from exc

    return SupportTriagePreviewOut(
        **preview,
        diagnostics_endpoint=f"/api/v1/admin/tenants/{company_id}/diagnostics",
        export_endpoint=f"/api/v1/admin/tenants/{company_id}/export",
        archive_delete_preview_endpoint=f"/api/v1/admin/tenants/{company_id}/archive-delete-preview?action=archive",
    )


__all__ = [
    "load_company_or_404",
    "build_archive_delete_preview_payload",
    "build_support_triage_preview_response",
]
