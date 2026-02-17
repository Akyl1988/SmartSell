"""Add repricing run items and rule fields.

Revision ID: 20260217_repricing_run_items
Revises: 20260217_kzt_integer_money
Create Date: 2026-02-17
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260217_repricing_run_items"
down_revision = "20260217_kzt_integer_money"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("repricing_rules", sa.Column("scope_type", sa.String(length=32), nullable=True))
    op.add_column("repricing_rules", sa.Column("scope_value", sa.String(length=255), nullable=True))
    op.add_column("repricing_rules", sa.Column("rounding_mode", sa.String(length=32), nullable=True))

    op.add_column("repricing_runs", sa.Column("processed", sa.Integer(), nullable=True))
    op.add_column("repricing_runs", sa.Column("changed", sa.Integer(), nullable=True))
    op.add_column("repricing_runs", sa.Column("failed", sa.Integer(), nullable=True))
    op.add_column("repricing_runs", sa.Column("last_error", sa.Text(), nullable=True))
    op.add_column("repricing_runs", sa.Column("triggered_by_user_id", sa.Integer(), nullable=True))
    op.alter_column("repricing_runs", "rule_id", existing_type=sa.Integer(), nullable=True)

    op.create_index(
        "ix_repricing_runs_company_status",
        "repricing_runs",
        ["company_id", "status"],
        unique=False,
    )
    op.create_index(
        "ix_repricing_runs_company_created",
        "repricing_runs",
        ["company_id", "created_at"],
        unique=False,
    )

    op.create_table(
        "repricing_run_items",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("product_id", sa.Integer(), nullable=True),
        sa.Column("old_price", sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column("new_price", sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column("reason", sa.String(length=255), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["run_id"], ["repricing_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_repricing_run_items_run",
        "repricing_run_items",
        ["run_id"],
        unique=False,
    )
    op.create_index(
        "ix_repricing_run_items_product",
        "repricing_run_items",
        ["product_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_repricing_run_items_product", table_name="repricing_run_items")
    op.drop_index("ix_repricing_run_items_run", table_name="repricing_run_items")
    op.drop_table("repricing_run_items")

    op.drop_index("ix_repricing_runs_company_created", table_name="repricing_runs")
    op.drop_index("ix_repricing_runs_company_status", table_name="repricing_runs")

    op.drop_column("repricing_runs", "triggered_by_user_id")
    op.drop_column("repricing_runs", "last_error")
    op.drop_column("repricing_runs", "failed")
    op.drop_column("repricing_runs", "changed")
    op.drop_column("repricing_runs", "processed")
    op.alter_column("repricing_runs", "rule_id", existing_type=sa.Integer(), nullable=False)

    op.drop_column("repricing_rules", "rounding_mode")
    op.drop_column("repricing_rules", "scope_value")
    op.drop_column("repricing_rules", "scope_type")
