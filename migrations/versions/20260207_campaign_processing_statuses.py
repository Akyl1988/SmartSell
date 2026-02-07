"""Campaign processing statuses and metadata

Revision ID: 20260207_campaign_processing_statuses
Revises: 20260206_kaspi_trial_grants
Create Date: 2026-02-07
"""

import sqlalchemy as sa
from alembic import op

try:
    from migrations.utils.pghelpers import safe_inspect
except Exception:  # pragma: no cover
    safe_inspect = None  # type: ignore

revision = "20260207_campaign_processing_statuses"
down_revision = "20260206_kaspi_trial_grants"
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

    if bind.dialect.name == "postgresql":
        for value in ("READY", "SCHEDULED", "RUNNING", "SUCCESS", "FAILED"):
            op.execute(f"ALTER TYPE campaign_status ADD VALUE IF NOT EXISTS '{value}'")

    if not insp or insp.has_table("campaigns"):
        if not insp or not _has_column(insp, "campaigns", "error_code"):
            op.add_column("campaigns", sa.Column("error_code", sa.String(length=64), nullable=True))
            op.create_index("ix_campaigns_error_code", "campaigns", ["error_code"])
        if not insp or not _has_column(insp, "campaigns", "error_message"):
            op.add_column("campaigns", sa.Column("error_message", sa.Text(), nullable=True))
        if not insp or not _has_column(insp, "campaigns", "request_id"):
            op.add_column("campaigns", sa.Column("request_id", sa.String(length=64), nullable=True))
            op.create_index("ix_campaigns_request_id", "campaigns", ["request_id"])


def downgrade() -> None:
    bind = op.get_bind()
    insp = safe_inspect(bind) if safe_inspect else None

    if not insp or insp.has_table("campaigns"):
        if not insp or _has_column(insp, "campaigns", "request_id"):
            op.drop_index("ix_campaigns_request_id", table_name="campaigns")
            op.drop_column("campaigns", "request_id")
        if not insp or _has_column(insp, "campaigns", "error_message"):
            op.drop_column("campaigns", "error_message")
        if not insp or _has_column(insp, "campaigns", "error_code"):
            op.drop_index("ix_campaigns_error_code", table_name="campaigns")
            op.drop_column("campaigns", "error_code")

    if bind.dialect.name == "postgresql":
        # Enum value removal is not supported safely; leave as-is.
        pass
