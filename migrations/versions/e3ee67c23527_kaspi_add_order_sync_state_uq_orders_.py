"""kaspi: add order sync state + uq orders external

Revision ID: e3ee67c23527
Revises: 20260102_wallet_and_payments
Create Date: 2026-01-05 04:24:16.037469+00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "e3ee67c23527"
down_revision: Union[str, Sequence[str], None] = "20260102_wallet_and_payments"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "kaspi_order_sync_state",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("company_id", sa.Integer(), sa.ForeignKey("companies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("last_synced_at", sa.DateTime(), nullable=True),
        sa.Column("last_external_order_id", sa.String(length=128), nullable=True),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("company_id", name="uq_kaspi_sync_state_company"),
    )
    op.create_unique_constraint("uq_orders_company_external_id", "orders", ["company_id", "external_id"])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint("uq_orders_company_external_id", "orders", type_="unique")
    op.drop_table("kaspi_order_sync_state")
