"""Refactor preorders for store module.

Revision ID: 20260217_preorders_store_module
Revises: 20260217_repricing_run_items
Create Date: 2026-02-17
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260217_preorders_store_module"
down_revision = "20260217_repricing_run_items"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("ALTER TYPE preorder_status RENAME VALUE 'created' TO 'new'")
        op.execute("ALTER TYPE preorder_status RENAME VALUE 'converted' TO 'fulfilled'")

    with op.batch_alter_table("preorders") as batch:
        batch.add_column(sa.Column("currency", sa.String(length=8), nullable=False, server_default="KZT"))
        batch.add_column(sa.Column("total", sa.Numeric(precision=14, scale=2), nullable=True))
        batch.add_column(sa.Column("notes", sa.Text(), nullable=True))
        batch.add_column(sa.Column("created_by_user_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk__preorders__created_by_user_id__users",
            "users",
            ["created_by_user_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch.alter_column("product_id", existing_type=sa.Integer(), nullable=True)
        batch.alter_column("qty", existing_type=sa.Integer(), nullable=True)
        batch.alter_column("status", existing_type=sa.Enum(name="preorder_status"), server_default="new")
        batch.drop_constraint("ck_preorders_qty_positive", type_="check")

    op.create_table(
        "preorder_items",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("preorder_id", sa.Integer(), nullable=False),
        sa.Column("product_id", sa.Integer(), nullable=True),
        sa.Column("sku", sa.String(length=100), nullable=True),
        sa.Column("name", sa.String(length=255), nullable=True),
        sa.Column("qty", sa.Integer(), nullable=False),
        sa.Column("price", sa.Numeric(precision=14, scale=2), nullable=True),
        sa.ForeignKeyConstraint(["preorder_id"], ["preorders.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_preorder_items_preorder", "preorder_items", ["preorder_id"], unique=False)
    op.create_index("ix_preorder_items_product", "preorder_items", ["product_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_preorder_items_product", table_name="preorder_items")
    op.drop_index("ix_preorder_items_preorder", table_name="preorder_items")
    op.drop_table("preorder_items")

    with op.batch_alter_table("preorders") as batch:
        batch.create_check_constraint("ck_preorders_qty_positive", "qty > 0")
        batch.alter_column("status", existing_type=sa.Enum(name="preorder_status"), server_default="created")
        batch.alter_column("qty", existing_type=sa.Integer(), nullable=False)
        batch.alter_column("product_id", existing_type=sa.Integer(), nullable=False)
        batch.drop_constraint("fk__preorders__created_by_user_id__users", type_="foreignkey")
        batch.drop_column("created_by_user_id")
        batch.drop_column("notes")
        batch.drop_column("total")
        batch.drop_column("currency")

    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("ALTER TYPE preorder_status RENAME VALUE 'new' TO 'created'")
        op.execute("ALTER TYPE preorder_status RENAME VALUE 'fulfilled' TO 'converted'")
