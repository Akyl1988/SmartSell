"""add deleted_at and ended_at to subscriptions

Revision ID: 20251228_add_subscriptions_deleted_at
Revises: 20251228_active_subscription_uniqueness
Create Date: 2025-12-28 00:30:00.000000
"""

from __future__ import annotations

from alembic import op, context
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "20251228_add_subscriptions_deleted_at"
down_revision = "20251228_active_subscription_uniqueness"
branch_labels = None
depends_on = None


ACTIVE_STATES_COND = "status IN ('active','trial','overdue','paused') AND deleted_at IS NULL"


def upgrade() -> None:
    if context.is_offline_mode():
        # Offline SQL generation cannot use Inspector on MockConnection; emit idempotent DDL directly
        op.execute(
            sa.text(
                "ALTER TABLE IF EXISTS subscriptions "
                "ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMP WITHOUT TIME ZONE"
            )
        )
        op.execute(
            sa.text(
                "ALTER TABLE IF EXISTS subscriptions "
                "ADD COLUMN IF NOT EXISTS ended_at TIMESTAMP WITH TIME ZONE"
            )
        )
        op.execute(
            sa.text(
                "CREATE INDEX IF NOT EXISTS ix_subscriptions_deleted_at "
                "ON subscriptions (deleted_at)"
            )
        )
        op.execute(
            sa.text(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_subscription_company_active_states "
                "ON subscriptions (company_id) WHERE " + ACTIVE_STATES_COND
            )
        )
        return

    bind = op.get_bind()
    insp = sa.inspect(bind)
    cols = {c["name"] for c in insp.get_columns("subscriptions")}

    if "deleted_at" not in cols:
        op.add_column(
            "subscriptions",
            sa.Column("deleted_at", sa.DateTime(timezone=False), nullable=True),
        )
    if "ended_at" not in cols:
        op.add_column(
            "subscriptions",
            sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        )

    # Ensure index on deleted_at exists
    idx_names = {idx["name"] for idx in insp.get_indexes("subscriptions")}
    if "ix_subscriptions_deleted_at" not in idx_names:
        op.create_index("ix_subscriptions_deleted_at", "subscriptions", ["deleted_at"])

    # Recreate partial unique index to ensure deleted_at is part of the predicate
    existing = insp.get_indexes("subscriptions")
    if any(idx.get("name") == "uq_subscription_company_active_states" for idx in existing):
        op.drop_index("uq_subscription_company_active_states", table_name="subscriptions")
    op.execute(
        sa.text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_subscription_company_active_states "
            "ON subscriptions (company_id) WHERE " + ACTIVE_STATES_COND
        )
    )


def downgrade() -> None:
    with op.batch_alter_table("subscriptions") as batch_op:
        batch_op.drop_index("uq_subscription_company_active_states")
        batch_op.create_index(
            "uq_subscription_company_active_states",
            ["company_id"],
            unique=True,
            postgresql_where=sa.text("status IN ('active','trial','overdue','paused')"),
        )

    op.drop_index("ix_subscriptions_deleted_at", table_name="subscriptions")
    op.drop_column("subscriptions", "ended_at")
    op.drop_column("subscriptions", "deleted_at")
