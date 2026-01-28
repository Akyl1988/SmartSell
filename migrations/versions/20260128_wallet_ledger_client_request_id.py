"""wallet_ledger client_request_id idempotency

Revision ID: 20260128_wallet_ledger_client_request_id
Revises: 20260127_users_hashed_password_text
Create Date: 2026-01-28
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "20260128_wallet_ledger_client_request_id"
down_revision = "20260127_users_hashed_password_text"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "wallet_ledger",
        sa.Column("client_request_id", sa.String(length=128), nullable=True),
        schema="public",
    )
    op.create_index(
        "ux_wallet_ledger_account_client_request_id",
        "wallet_ledger",
        ["account_id", "client_request_id"],
        unique=True,
        schema="public",
        postgresql_where=sa.text("client_request_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "ux_wallet_ledger_account_client_request_id",
        table_name="wallet_ledger",
        schema="public",
    )
    op.drop_column("wallet_ledger", "client_request_id", schema="public")
