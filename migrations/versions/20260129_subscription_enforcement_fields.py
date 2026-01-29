"""Add subscription enforcement fields

Revision ID: 20260129_subscription_enforcement_fields
Revises: 20260129_payment_intents
Create Date: 2026-01-29
"""

import sqlalchemy as sa
from alembic import op

try:
    from migrations.utils.pghelpers import safe_inspect
except Exception:  # pragma: no cover - fallback if utils import fails
    safe_inspect = None  # type: ignore

revision = "20260129_subscription_enforcement_fields"
down_revision = "20260129_payment_intents"
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

    if not insp or insp.has_table("subscriptions"):
        if not insp or not _has_column(insp, "subscriptions", "period_start"):
            op.add_column("subscriptions", sa.Column("period_start", sa.DateTime(timezone=True), nullable=True))
        if not insp or not _has_column(insp, "subscriptions", "period_end"):
            op.add_column("subscriptions", sa.Column("period_end", sa.DateTime(timezone=True), nullable=True))
            op.create_index("ix_subscriptions_period_end", "subscriptions", ["period_end"], unique=False)
        if not insp or not _has_column(insp, "subscriptions", "cancel_at_period_end"):
            op.add_column(
                "subscriptions",
                sa.Column("cancel_at_period_end", sa.Boolean(), nullable=False, server_default="false"),
            )
        if not insp or not _has_column(insp, "subscriptions", "frozen_at"):
            op.add_column("subscriptions", sa.Column("frozen_at", sa.DateTime(timezone=True), nullable=True))
        if not insp or not _has_column(insp, "subscriptions", "resumed_at"):
            op.add_column("subscriptions", sa.Column("resumed_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    insp = safe_inspect(bind) if safe_inspect else None

    if not insp or insp.has_table("subscriptions"):
        for idx in ("ix_subscriptions_period_end",):
            try:
                op.drop_index(idx, table_name="subscriptions")
            except Exception:
                pass

        for col in ("resumed_at", "frozen_at", "cancel_at_period_end", "period_end", "period_start"):
            try:
                op.drop_column("subscriptions", col)
            except Exception:
                pass
