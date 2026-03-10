from __future__ import annotations

from datetime import datetime

from app.schemas.base import BaseSchema


class TenantExportSectionCount(BaseSchema):
    section: str
    count: int


class TenantExportSummaryOut(BaseSchema):
    included_sections: list[str]
    section_counts: dict[str, int]
    warnings: list[str]
    not_included: list[str]


class TenantExportManifestOut(BaseSchema):
    company_id: int
    company_name: str
    exported_at: datetime
    exported_by: str | None = None
    export_scope_version: str
    included_sections: list[str]
    section_counts: dict[str, int]
    warnings: list[str]
    not_included: list[str]
