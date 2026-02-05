"""Subscription anchor/grace + wallet topup idempotency

Revision ID: 20260206_subscription_billing_anchor_grace_wallet_tx_idempotent
Revises: 20260205_idempotency_keys
Create Date: 2026-02-06
"""

import sqlalchemy as sa
from alembic import op

try:
    from migrations.utils.pghelpers import safe_inspect
except Exception:  # pragma: no cover - fallback if utils import fails
    safe_inspect = None  # type: ignore

revision = "20260206_subscription_billing_anchor_grace_wallet_tx_idempotent"
down_revision = "20260205_idempotency_keys"
branch_labels = None
depends_on = None


def _has_column(insp, table: str, column: str) -> bool:
    try:
        return any(col.get("name") == column for col in insp.get_columns(table))
    except Exception:
        return False


def _has_index(insp, table: str, index: str) -> bool:
    try:
        return any(idx.get("name") == index for idx in insp.get_indexes(table))
    except Exception:
        return False


def upgrade() -> None:
    bind = op.get_bind()
    insp = safe_inspect(bind) if safe_inspect else None

    if not insp or insp.has_table("subscriptions"):
        if not insp or not _has_column(insp, "subscriptions", "billing_anchor_day"):
            op.add_column("subscriptions", sa.Column("billing_anchor_day", sa.Integer(), nullable=True))
        if not insp or not _has_column(insp, "subscriptions", "grace_until"):
            op.add_column("subscriptions", sa.Column("grace_until", sa.DateTime(timezone=True), nullable=True))
            if not insp or not _has_index(insp, "subscriptions", "ix_subscriptions_grace_until"):
                op.create_index("ix_subscriptions_grace_until", "subscriptions", ["grace_until"], unique=False)
        try:
            op.create_check_constraint(
                "ck_subscription_anchor_day",
                "subscriptions",
                "billing_anchor_day IS NULL OR (billing_anchor_day >= 1 AND billing_anchor_day <= 31)",
            )
        except Exception:
            pass

    if not insp or insp.has_table("wallet_transactions"):
        if not insp or _has_index(insp, "wallet_transactions", "ix_wallet_transaction_wallet_client_request"):
            try:
                op.drop_index("ix_wallet_transaction_wallet_client_request", table_name="wallet_transactions")
            except Exception:
                pass
        if not insp or not _has_index(insp, "wallet_transactions", "uq_wallet_transaction_wallet_client_request"):
            op.create_index(
                "uq_wallet_transaction_wallet_client_request",
                "wallet_transactions",
                ["wallet_id", "client_request_id"],
                unique=True,
                postgresql_where=sa.text("client_request_id IS NOT NULL"),
            )


def downgrade() -> None:
    bind = op.get_bind()
    insp = safe_inspect(bind) if safe_inspect else None

    if not insp or insp.has_table("wallet_transactions"):
        try:
            op.drop_index("uq_wallet_transaction_wallet_client_request", table_name="wallet_transactions")
        except Exception:
            pass
        try:
            op.create_index(
                "ix_wallet_transaction_wallet_client_request",
                "wallet_transactions",
                ["wallet_id", "client_request_id"],
                unique=False,
            )
        except Exception:
            pass

    if not insp or insp.has_table("subscriptions"):
        try:
            op.drop_constraint("ck_subscription_anchor_day", "subscriptions", type_="check")
        except Exception:
            pass
        try:
            op.drop_index("ix_subscriptions_grace_until", table_name="subscriptions")
        except Exception:
            pass
        for col in ("grace_until", "billing_anchor_day"):
            try:
                op.drop_column("subscriptions", col)
            except Exception:
                pass
