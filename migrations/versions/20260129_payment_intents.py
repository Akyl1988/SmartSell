"""Add payment intents table

Revision ID: 20260129_payment_intents
Revises: 20260128_wallet_ledger_client_request_id
Create Date: 2026-01-29
"""

import sqlalchemy as sa
from alembic import op

try:
    from migrations.utils.pghelpers import safe_inspect
except Exception:  # pragma: no cover - fallback if utils import fails
    safe_inspect = None  # type: ignore

revision = "20260129_payment_intents"
down_revision = "20260128_wallet_ledger_client_request_id"
branch_labels = None
depends_on = None


_DECIMAL_PLACES = 6


def upgrade() -> None:
    bind = op.get_bind()
    insp = safe_inspect(bind) if safe_inspect else None

    if not insp or not insp.has_table("payment_intents"):
        op.create_table(
            "payment_intents",
            sa.Column("id", sa.String(length=64), primary_key=True),
            sa.Column("company_id", sa.Integer(), nullable=False),
            sa.Column("provider", sa.String(length=64), nullable=False),
            sa.Column("provider_version", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("status", sa.String(length=32), nullable=False),
            sa.Column("amount", sa.Numeric(18, _DECIMAL_PLACES), nullable=False),
            sa.Column("currency", sa.String(length=10), nullable=False),
            sa.Column("customer_id", sa.String(length=128), nullable=False),
            sa.Column("provider_intent_id", sa.String(length=128), nullable=False),
            sa.Column("metadata_json", sa.Text(), nullable=True),
            sa.Column("created_at", sa.String(length=40), nullable=False),
            sa.Column("updated_at", sa.String(length=40), nullable=False),
        )
        op.create_index("ix_payment_intents_company_id", "payment_intents", ["company_id"], unique=False)
        op.create_index("ix_payment_intents_status", "payment_intents", ["status"], unique=False)
        op.create_index("ix_payment_intents_currency", "payment_intents", ["currency"], unique=False)
        op.create_index("ix_payment_intents_customer_id", "payment_intents", ["customer_id"], unique=False)
        op.create_index("ix_payment_intents_provider_intent_id", "payment_intents", ["provider_intent_id"], unique=False)


def downgrade() -> None:
    bind = op.get_bind()
    insp = safe_inspect(bind) if safe_inspect else None

    if not insp or insp.has_table("payment_intents"):
        for idx in (
            "ix_payment_intents_provider_intent_id",
            "ix_payment_intents_customer_id",
            "ix_payment_intents_currency",
            "ix_payment_intents_status",
            "ix_payment_intents_company_id",
        ):
            try:
                op.drop_index(idx, table_name="payment_intents")
            except Exception:
                pass
        op.drop_table("payment_intents")
