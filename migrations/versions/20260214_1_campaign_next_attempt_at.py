"""Add campaign next_attempt_at backoff field.

Revision ID: 20260214_campaign_next_attempt_at
Revises: 20260214_campaign_run_metadata
Create Date: 2026-02-14
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

try:
    from migrations.utils.pghelpers import safe_inspect
except Exception:  # pragma: no cover
    safe_inspect = None  # type: ignore

revision = "20260214_campaign_next_attempt_at"
down_revision = "20260214_campaign_run_metadata"
branch_labels = None
depends_on = None


def _has_column(insp, table: str, column: str) -> bool:
    try:
        return any(col.get("name") == column for col in insp.get_columns(table))
    except Exception:
        return False


def _has_index(insp, table: str, index_name: str) -> bool:
    try:
        return any(idx.get("name") == index_name for idx in insp.get_indexes(table))
    except Exception:
        return False


def upgrade() -> None:
    bind = op.get_bind()
    insp = safe_inspect(bind) if safe_inspect else None

    if not insp or insp.has_table("campaigns"):
        if not insp or not _has_column(insp, "campaigns", "next_attempt_at"):
            op.add_column("campaigns", sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True))
        if bind.dialect.name == "postgresql":
            op.execute(
                "CREATE INDEX IF NOT EXISTS ix_campaign_processing_next_attempt_at "
                "ON campaigns (processing_status, next_attempt_at)"
            )
        elif not insp or not _has_index(insp, "campaigns", "ix_campaign_processing_next_attempt_at"):
            op.create_index(
                "ix_campaign_processing_next_attempt_at",
                "campaigns",
                ["processing_status", "next_attempt_at"],
            )


def downgrade() -> None:
    bind = op.get_bind()
    insp = safe_inspect(bind) if safe_inspect else None

    if not insp or insp.has_table("campaigns"):
        if bind.dialect.name == "postgresql":
            op.execute("DROP INDEX IF EXISTS ix_campaign_processing_next_attempt_at")
        elif not insp or _has_index(insp, "campaigns", "ix_campaign_processing_next_attempt_at"):
            op.drop_index("ix_campaign_processing_next_attempt_at", table_name="campaigns")
        if not insp or _has_column(insp, "campaigns", "next_attempt_at"):
            op.drop_column("campaigns", "next_attempt_at")
