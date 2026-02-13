"""Add campaign run metadata fields.

Revision ID: 20260214_campaign_run_metadata
Revises: 20260213_user_role_platform_manager
Create Date: 2026-02-14
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

try:
    from migrations.utils.pghelpers import safe_inspect
except Exception:  # pragma: no cover
    safe_inspect = None  # type: ignore

revision = "20260214_campaign_run_metadata"
down_revision = "20260213_user_role_platform_manager"
branch_labels = None
depends_on = None


def _has_column(insp, table: str, column: str) -> bool:
    try:
        return any(col.get("name") == column for col in insp.get_columns(table))
    except Exception:
        return False


def upgrade() -> None:
    bind = op.get_bind()
    insp = safe_inspect(bind) if safe_inspect else None

    if not insp or insp.has_table("campaigns"):
        if not insp or not _has_column(insp, "campaigns", "failed_at"):
            op.add_column("campaigns", sa.Column("failed_at", sa.DateTime(timezone=True), nullable=True))
        if not insp or not _has_column(insp, "campaigns", "requested_by_user_id"):
            op.add_column("campaigns", sa.Column("requested_by_user_id", sa.Integer(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    insp = safe_inspect(bind) if safe_inspect else None

    if not insp or insp.has_table("campaigns"):
        if not insp or _has_column(insp, "campaigns", "requested_by_user_id"):
            op.drop_column("campaigns", "requested_by_user_id")
        if not insp or _has_column(insp, "campaigns", "failed_at"):
            op.drop_column("campaigns", "failed_at")
