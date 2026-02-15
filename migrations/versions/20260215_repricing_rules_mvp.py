"""Add repricing rules and run logs.

Revision ID: 20260215_repricing_rules_mvp
Revises: 20260214_campaign_next_attempt_at
Create Date: 2026-02-15
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260215_repricing_rules_mvp"
down_revision = "20260214_campaign_next_attempt_at"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "repricing_rules",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("scope", sa.JSON(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("min_price", sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column("max_price", sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column("step", sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column("undercut", sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column("cooldown_seconds", sa.Integer(), nullable=True),
        sa.Column("max_delta_percent", sa.Numeric(precision=5, scale=2), nullable=True),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_repricing_rules_company_active",
        "repricing_rules",
        ["company_id", "is_active"],
        unique=False,
    )

    op.create_table(
        "repricing_runs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("rule_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default=sa.text("'running'")),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("stats", sa.JSON(), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("requested_by_user_id", sa.Integer(), nullable=True),
        sa.Column("request_id", sa.String(length=64), nullable=True),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["rule_id"], ["repricing_rules.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_repricing_runs_company_rule",
        "repricing_runs",
        ["company_id", "rule_id"],
        unique=False,
    )

    op.create_table(
        "repricing_diffs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("rule_id", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("product_id", sa.Integer(), nullable=True),
        sa.Column("sku", sa.String(length=100), nullable=True),
        sa.Column("old_price", sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column("new_price", sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column("reason", sa.String(length=255), nullable=True),
        sa.Column("meta", sa.JSON(), nullable=True),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["rule_id"], ["repricing_rules.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["run_id"], ["repricing_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_repricing_diffs_company_run",
        "repricing_diffs",
        ["company_id", "run_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_repricing_diffs_company_run", table_name="repricing_diffs")
    op.drop_table("repricing_diffs")

    op.drop_index("ix_repricing_runs_company_rule", table_name="repricing_runs")
    op.drop_table("repricing_runs")

    op.drop_index("ix_repricing_rules_company_active", table_name="repricing_rules")
    op.drop_table("repricing_rules")
