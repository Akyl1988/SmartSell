"""Subscription overrides

Revision ID: 20260203_subscription_overrides
Revises: 20260130_kaspi_goods_imports_official_columns
Create Date: 2026-02-03
"""

import sqlalchemy as sa
from alembic import op

try:
    from migrations.utils.pghelpers import safe_inspect
except Exception:  # pragma: no cover - fallback if utils import fails
    safe_inspect = None  # type: ignore

revision = "20260203_subscription_overrides"
down_revision = "20260130_kaspi_goods_imports_official_columns"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = safe_inspect(bind) if safe_inspect else None

    if not insp or not insp.has_table("subscription_overrides"):
        op.create_table(
            "subscription_overrides",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("provider", sa.String(length=32), nullable=False, server_default="kaspi"),
            sa.Column("company_id", sa.Integer(), sa.ForeignKey("companies.id", ondelete="CASCADE"), nullable=False),
            sa.Column("merchant_uid", sa.String(length=128), nullable=False),
            sa.Column("active_until", sa.DateTime(timezone=True), nullable=True),
            sa.Column("note", sa.Text(), nullable=True),
            sa.Column("created_by_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("now()"),
            ),
            sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
            sa.UniqueConstraint(
                "company_id",
                "provider",
                "merchant_uid",
                name="uq_subscription_overrides_company_provider_merchant",
            ),
        )
        op.create_index(
            "ix_subscription_overrides_provider_merchant",
            "subscription_overrides",
            ["provider", "merchant_uid"],
            unique=False,
        )
        op.create_index(
            "ix_subscription_overrides_company_id",
            "subscription_overrides",
            ["company_id"],
            unique=False,
        )


def downgrade() -> None:
    bind = op.get_bind()
    insp = safe_inspect(bind) if safe_inspect else None

    if not insp or insp.has_table("subscription_overrides"):
        try:
            op.drop_index("ix_subscription_overrides_company_id", table_name="subscription_overrides")
        except Exception:
            pass
        try:
            op.drop_index("ix_subscription_overrides_provider_merchant", table_name="subscription_overrides")
        except Exception:
            pass
        op.drop_table("subscription_overrides")
