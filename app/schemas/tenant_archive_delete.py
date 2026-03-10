from __future__ import annotations

from typing import Literal

from app.schemas.base import BaseSchema


class TenantArchiveDeletePreviewOut(BaseSchema):
    company_id: int
    current_state: str
    requested_action: Literal["archive", "delete"]
    allowed: bool
    required_before_action: list[str]
    warnings: list[str]
    next_state: str
    destructive_delete_supported: bool = False
