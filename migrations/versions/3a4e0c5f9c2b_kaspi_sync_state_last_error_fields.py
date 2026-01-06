"""kaspi: add last error fields to sync state

Revision ID: 3a4e0c5f9c2b
Revises: 29a2929fc59b
Create Date: 2026-01-06 21:10:00.000000+00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "3a4e0c5f9c2b"
down_revision: Union[str, Sequence[str], None] = "29a2929fc59b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("kaspi_order_sync_state", sa.Column("last_error_at", sa.DateTime(), nullable=True))
    op.add_column("kaspi_order_sync_state", sa.Column("last_error_code", sa.String(length=64), nullable=True))
    op.add_column("kaspi_order_sync_state", sa.Column("last_error_message", sa.String(length=500), nullable=True))


def downgrade() -> None:
    op.drop_column("kaspi_order_sync_state", "last_error_message")
    op.drop_column("kaspi_order_sync_state", "last_error_code")
    op.drop_column("kaspi_order_sync_state", "last_error_at")
