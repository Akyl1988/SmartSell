"""Add wallet and payments tables

Revision ID: 20260102_wallet_and_payments
Revises: 20251228_subs_deleted_at
Create Date: 2026-01-02 13:25:00.000000
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

try:
    from migrations.utils.pghelpers import safe_inspect
except Exception:  # pragma: no cover - fallback if utils import fails
    safe_inspect = None  # type: ignore


# revision identifiers, used by Alembic.
revision = "20260102_wallet_and_payments"
down_revision = "20251228_subs_deleted_at"
branch_labels = None
depends_on = None


_DECIMAL_PLACES = 6


def upgrade():
    bind = op.get_bind()
    insp = safe_inspect(bind) if safe_inspect else inspect(bind)

    if not insp or not insp.has_table("wallet_accounts"):
        op.create_table(
            "wallet_accounts",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("currency", sa.String(length=10), nullable=False),
            sa.Column("balance", sa.Numeric(18, _DECIMAL_PLACES), nullable=False, server_default="0"),
            sa.Column("created_at", sa.String(length=40), nullable=False),
            sa.Column("updated_at", sa.String(length=40), nullable=False),
            sa.UniqueConstraint("user_id", "currency", name="uq_wallet_accounts_user_currency"),
        )
        op.create_index("ix_wallet_accounts_user_id", "wallet_accounts", ["user_id"], unique=False)
        op.create_index("ix_wallet_accounts_currency", "wallet_accounts", ["currency"], unique=False)
        op.create_index("ix_wallet_accounts_user_currency", "wallet_accounts", ["user_id", "currency"], unique=False)
        op.create_index("ix_wallet_accounts_updated_at", "wallet_accounts", ["updated_at"], unique=False)

    if not insp or not insp.has_table("wallet_ledger"):
        op.create_table(
            "wallet_ledger",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("account_id", sa.Integer(), sa.ForeignKey("wallet_accounts.id", ondelete="CASCADE"), nullable=False),
            sa.Column("entry_type", sa.String(length=20), nullable=False),
            sa.Column("amount", sa.Numeric(18, _DECIMAL_PLACES), nullable=False),
            sa.Column("currency", sa.String(length=10), nullable=False),
            sa.Column("reference", sa.String(length=255), nullable=True),
            sa.Column("created_at", sa.String(length=40), nullable=False),
        )
        op.create_index("ix_wallet_ledger_account_id", "wallet_ledger", ["account_id"], unique=False)
        op.create_index("ix_wallet_ledger_created_at", "wallet_ledger", ["created_at"], unique=False)

    if not insp or not insp.has_table("wallet_payments"):
        op.create_table(
            "wallet_payments",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("wallet_account_id", sa.Integer(), nullable=False),
            sa.Column("amount", sa.Numeric(18, _DECIMAL_PLACES), nullable=False),
            sa.Column("currency", sa.String(length=10), nullable=False),
            sa.Column("status", sa.String(length=20), nullable=False),
            sa.Column("refund_amount", sa.Numeric(18, _DECIMAL_PLACES), nullable=False, server_default="0"),
            sa.Column("reference", sa.Text(), nullable=True),
            sa.Column("created_at", sa.String(length=40), nullable=False),
            sa.Column("updated_at", sa.String(length=40), nullable=False),
        )
        op.create_index("ix_wallet_payments_user_id", "wallet_payments", ["user_id"], unique=False)
        op.create_index("ix_wallet_payments_wallet_account_id", "wallet_payments", ["wallet_account_id"], unique=False)
        op.create_index("ix_wallet_payments_currency", "wallet_payments", ["currency"], unique=False)
        op.create_index("ix_wallet_payments_status", "wallet_payments", ["status"], unique=False)


def downgrade():
    bind = op.get_bind()
    insp = safe_inspect(bind) if safe_inspect else inspect(bind)

    if not insp or insp.has_table("wallet_payments"):
        for idx in (
            "ix_wallet_payments_status",
            "ix_wallet_payments_currency",
            "ix_wallet_payments_wallet_account_id",
            "ix_wallet_payments_user_id",
        ):
            try:
                op.drop_index(idx, table_name="wallet_payments")
            except Exception:
                pass
        op.drop_table("wallet_payments")

    if not insp or insp.has_table("wallet_ledger"):
        for idx in ("ix_wallet_ledger_created_at", "ix_wallet_ledger_account_id"):
            try:
                op.drop_index(idx, table_name="wallet_ledger")
            except Exception:
                pass
        op.drop_table("wallet_ledger")

    if not insp or insp.has_table("wallet_accounts"):
        for idx in (
            "ix_wallet_accounts_updated_at",
            "ix_wallet_accounts_user_currency",
            "ix_wallet_accounts_currency",
            "ix_wallet_accounts_user_id",
        ):
            try:
                op.drop_index(idx, table_name="wallet_accounts")
            except Exception:
                pass
        op.drop_table("wallet_accounts")
