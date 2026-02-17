"""Add preorders table.

Revision ID: 20260215_preorders_mvp
Revises: 20260215_repricing_rules_mvp
Create Date: 2026-02-15
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260215_preorders_mvp"
down_revision = "20260215_repricing_rules_mvp"
branch_labels = None
depends_on = None

_STATUS_ENUM = sa.Enum(
    "created",
    "confirmed",
    "cancelled",
    "converted",
    name="preorder_status",
)


def upgrade() -> None:
    op.create_table(
        "preorders",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("product_id", sa.Integer(), nullable=False),
        sa.Column("qty", sa.Integer(), nullable=False),
        sa.Column("customer_name", sa.String(length=255), nullable=True),
        sa.Column("customer_phone", sa.String(length=32), nullable=True),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("status", _STATUS_ENUM, nullable=False, server_default=sa.text("'created'")),
        sa.Column("preorder_until_snapshot", sa.DateTime(), nullable=True),
        sa.Column("deposit_snapshot", sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column("converted_order_id", sa.Integer(), nullable=True),
        sa.CheckConstraint("qty > 0", name="ck_preorders_qty_positive"),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["converted_order_id"], ["orders.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_preorders_company_created", "preorders", ["company_id", "created_at"], unique=False)
    op.create_index("ix_preorders_company_status", "preorders", ["company_id", "status"], unique=False)
    op.create_index("ix_preorders_company_product", "preorders", ["company_id", "product_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_preorders_company_product", table_name="preorders")
    op.drop_index("ix_preorders_company_status", table_name="preorders")
    op.drop_index("ix_preorders_company_created", table_name="preorders")
    op.drop_table("preorders")
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("DROP TYPE IF EXISTS preorder_status")
