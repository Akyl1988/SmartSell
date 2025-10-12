# alembic/versions/20251003_create_inventory_outbox.py
import sqlalchemy as sa

from alembic import op

revision = "20251003_create_inventory_outbox"
down_revision = "20251003_create_customers"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "inventory_outbox",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("aggregate_type", sa.String(64), nullable=False),
        sa.Column("aggregate_id", sa.String(64), nullable=False),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("payload", sa.Text, nullable=False),
        sa.Column("channel", sa.String(64), nullable=False, server_default="erp"),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer, nullable=False, server_default="0"),
        sa.Column("next_attempt_at", sa.DateTime),
        sa.Column("last_error", sa.Text),
        sa.Column("processed_at", sa.DateTime),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime, nullable=False, server_default=sa.text("now()")),
        sa.Column("deleted_at", sa.DateTime),
    )
    op.create_index("ix_outbox_status", "inventory_outbox", ["status"])
    op.create_index("ix_outbox_event", "inventory_outbox", ["event_type"])
    op.create_index("ix_outbox_aggregate", "inventory_outbox", ["aggregate_type", "aggregate_id"])


def downgrade():
    op.drop_table("inventory_outbox")
