"""Add fulfillment order linkage for preorders.

Revision ID: 20260217_preorders_fulfillment_order
Revises: 20260217_preorders_store_module
Create Date: 2026-02-17
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260217_preorders_fulfillment_order"
down_revision = "20260217_preorders_store_module"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("ALTER TYPE ordersource RENAME VALUE 'KASPI' TO 'kaspi'")
        op.execute("ALTER TYPE ordersource RENAME VALUE 'WEBSITE' TO 'website'")
        op.execute("ALTER TYPE ordersource RENAME VALUE 'MANUAL' TO 'manual'")
        op.execute("ALTER TYPE ordersource RENAME VALUE 'API' TO 'api'")
        op.execute("ALTER TYPE ordersource ADD VALUE IF NOT EXISTS 'preorder'")

    with op.batch_alter_table("preorders") as batch:
        batch.add_column(sa.Column("fulfilled_order_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("fulfilled_at", sa.DateTime(), nullable=True))
        batch.create_foreign_key(
            "fk__preorders__fulfilled_order_id__orders",
            "orders",
            ["fulfilled_order_id"],
            ["id"],
            ondelete="SET NULL",
        )
    op.create_index("ix_preorders_fulfilled_order", "preorders", ["fulfilled_order_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_preorders_fulfilled_order", table_name="preorders")
    with op.batch_alter_table("preorders") as batch:
        batch.drop_constraint("fk__preorders__fulfilled_order_id__orders", type_="foreignkey")
        batch.drop_column("fulfilled_at")
        batch.drop_column("fulfilled_order_id")
