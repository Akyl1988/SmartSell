"""Kaspi trial grants

Revision ID: 20260206_kaspi_trial_grants
Revises: 20260206_subscription_billing_anchor_grace_wallet_tx_idempotent
Create Date: 2026-02-06
"""

import sqlalchemy as sa
from alembic import op

try:
    from migrations.utils.pghelpers import safe_inspect
except Exception:  # pragma: no cover - fallback if utils import fails
    safe_inspect = None  # type: ignore

revision = "20260206_kaspi_trial_grants"
down_revision = "20260206_subscription_billing_anchor_grace_wallet_tx_idempotent"
branch_labels = None
depends_on = None


def _has_table(insp, table: str) -> bool:
    try:
        return insp.has_table(table)
    except Exception:
        return False


def upgrade() -> None:
    bind = op.get_bind()
    insp = safe_inspect(bind) if safe_inspect else None

    if not insp or not _has_table(insp, "kaspi_trial_grants"):
        op.create_table(
            "kaspi_trial_grants",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("provider", sa.String(length=32), nullable=False, server_default="kaspi"),
            sa.Column("merchant_uid", sa.String(length=128), nullable=False),
            sa.Column("company_id", sa.Integer(), sa.ForeignKey("companies.id", ondelete="CASCADE"), nullable=False),
            sa.Column("subscription_id", sa.Integer(), sa.ForeignKey("subscriptions.id", ondelete="SET NULL")),
            sa.Column("granted_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.Column("trial_ends_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("status", sa.String(length=32), nullable=False, server_default="active"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("now()"),
            ),
        )
        op.create_index(
            "uq_kaspi_trial_grants_provider_merchant",
            "kaspi_trial_grants",
            ["provider", "merchant_uid"],
            unique=True,
        )
        op.create_index(
            "ix_kaspi_trial_grants_merchant_uid",
            "kaspi_trial_grants",
            ["merchant_uid"],
            unique=False,
        )


def downgrade() -> None:
    op.drop_index("ix_kaspi_trial_grants_merchant_uid", table_name="kaspi_trial_grants")
    op.drop_index("uq_kaspi_trial_grants_provider_merchant", table_name="kaspi_trial_grants")
    op.drop_table("kaspi_trial_grants")
