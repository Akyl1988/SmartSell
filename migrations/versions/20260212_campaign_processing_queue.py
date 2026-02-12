"""Campaign processing queue fields

Revision ID: 20260212_campaign_processing_queue
Revises: 20260207_campaign_processing_statuses
Create Date: 2026-02-12
"""

import sqlalchemy as sa
from alembic import op

try:
    from migrations.utils.pghelpers import safe_inspect
except Exception:  # pragma: no cover
    safe_inspect = None  # type: ignore

revision = "20260212_campaign_processing_queue"
down_revision = "20260207_campaign_processing_statuses"
branch_labels = None
depends_on = None


_STATUS_ENUM = sa.Enum(
    "queued",
    "processing",
    "done",
    "failed",
    name="campaign_processing_status",
)


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

    if bind.dialect.name == "postgresql":
        _STATUS_ENUM.create(bind, checkfirst=True)

    if not insp or insp.has_table("campaigns"):
        if not insp or not _has_column(insp, "campaigns", "queued_at"):
            op.add_column("campaigns", sa.Column("queued_at", sa.DateTime(timezone=True), nullable=True))
        if not insp or not _has_column(insp, "campaigns", "processing_status"):
            op.add_column(
                "campaigns",
                sa.Column(
                    "processing_status",
                    _STATUS_ENUM,
                    nullable=False,
                    server_default="done",
                ),
            )
        if bind.dialect.name == "postgresql":
            op.execute(
                "CREATE INDEX IF NOT EXISTS ix_campaign_processing_status_queued_at "
                "ON campaigns (processing_status, queued_at)"
            )
        elif not insp or not _has_index(insp, "campaigns", "ix_campaign_processing_status_queued_at"):
            op.create_index(
                "ix_campaign_processing_status_queued_at",
                "campaigns",
                ["processing_status", "queued_at"],
            )
        if not insp or not _has_column(insp, "campaigns", "started_at"):
            op.add_column("campaigns", sa.Column("started_at", sa.DateTime(timezone=True), nullable=True))
        if not insp or not _has_column(insp, "campaigns", "finished_at"):
            op.add_column("campaigns", sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True))
        if not insp or not _has_column(insp, "campaigns", "last_error"):
            op.add_column("campaigns", sa.Column("last_error", sa.Text(), nullable=True))
        if not insp or not _has_column(insp, "campaigns", "attempts"):
            op.add_column(
                "campaigns",
                sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
            )


def downgrade() -> None:
    bind = op.get_bind()
    insp = safe_inspect(bind) if safe_inspect else None

    if not insp or insp.has_table("campaigns"):
        if not insp or _has_column(insp, "campaigns", "attempts"):
            op.drop_column("campaigns", "attempts")
        if not insp or _has_column(insp, "campaigns", "last_error"):
            op.drop_column("campaigns", "last_error")
        if not insp or _has_column(insp, "campaigns", "finished_at"):
            op.drop_column("campaigns", "finished_at")
        if not insp or _has_column(insp, "campaigns", "started_at"):
            op.drop_column("campaigns", "started_at")
        if bind.dialect.name == "postgresql":
            op.execute("DROP INDEX IF EXISTS ix_campaign_processing_status_queued_at")
        elif not insp or _has_index(insp, "campaigns", "ix_campaign_processing_status_queued_at"):
            op.drop_index("ix_campaign_processing_status_queued_at", table_name="campaigns")
        if not insp or _has_column(insp, "campaigns", "queued_at"):
            op.drop_column("campaigns", "queued_at")
        if not insp or _has_column(insp, "campaigns", "processing_status"):
            op.drop_column("campaigns", "processing_status")

    if bind.dialect.name == "postgresql":
        _STATUS_ENUM.drop(bind, checkfirst=True)
