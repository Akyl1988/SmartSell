"""Add plans, features, and feature usage tables.

Revision ID: 20260216_plans_features_usage
Revises: 20260215_preorders_mvp
Create Date: 2026-02-16
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260216_plans_features_usage"
down_revision = "20260215_preorders_mvp"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "plans",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("code", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("price", sa.Numeric(precision=14, scale=2), nullable=False, server_default=sa.text("0")),
        sa.Column("currency", sa.String(length=8), nullable=False, server_default=sa.text("'KZT'")),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("trial_days_default", sa.Integer(), nullable=False, server_default=sa.text("14")),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code", name="uq_plans_code"),
    )
    op.create_index("ix_plans_code", "plans", ["code"], unique=True)

    op.create_table(
        "features",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("description", sa.String(length=512), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code", name="uq_features_code"),
    )
    op.create_index("ix_features_code", "features", ["code"], unique=True)

    op.create_table(
        "plan_features",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("plan_id", sa.Integer(), nullable=False),
        sa.Column("feature_id", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("limits_json", sa.JSON(), nullable=True),
        sa.ForeignKeyConstraint(["feature_id"], ["features.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["plan_id"], ["plans.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("plan_id", "feature_id", name="uq_plan_features_plan_feature"),
    )
    op.create_index("ix_plan_features_plan_feature", "plan_features", ["plan_id", "feature_id"], unique=False)

    op.create_table(
        "feature_usage",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("feature_id", sa.Integer(), nullable=False),
        sa.Column("subscription_id", sa.Integer(), nullable=False),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("period_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["feature_id"], ["features.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["subscription_id"], ["subscriptions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "company_id",
            "feature_id",
            "subscription_id",
            name="uq_feature_usage_company_feature_subscription",
        ),
    )
    op.create_index(
        "ix_feature_usage_company_feature",
        "feature_usage",
        ["company_id", "feature_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_feature_usage_company_feature", table_name="feature_usage")
    op.drop_table("feature_usage")
    op.drop_index("ix_plan_features_plan_feature", table_name="plan_features")
    op.drop_table("plan_features")
    op.drop_index("ix_features_code", table_name="features")
    op.drop_table("features")
    op.drop_index("ix_plans_code", table_name="plans")
    op.drop_table("plans")
