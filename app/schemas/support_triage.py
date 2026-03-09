from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.base import BaseSchema


class SupportTriagePreviewIn(BaseModel):
    severity: str = Field(..., min_length=5, max_length=8)
    area: str = Field(..., min_length=3, max_length=32)
    issue_summary: str = Field(..., min_length=3, max_length=2000)
    latest_request_id: str | None = Field(default=None, max_length=128)


class SupportTriagePreviewOut(BaseSchema):
    company_id: int
    severity: str
    area: str
    issue_summary: str
    normalized: bool
    required_inputs: list[str]
    recommended_next_steps: list[str]
    diagnostics_endpoint: str
    export_endpoint: str
    archive_delete_preview_endpoint: str
    status: Literal["preview"] = "preview"
    automation_supported: bool = False
