from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "e94854a3fe4b"
down_revision = "3a4e0c5f9c2b"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ONLY: kaspi_order_sync_state metrics columns
    op.add_column("kaspi_order_sync_state", sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("kaspi_order_sync_state", sa.Column("last_duration_ms", sa.Integer(), nullable=True))
    op.add_column("kaspi_order_sync_state", sa.Column("last_result", sa.String(length=32), nullable=True))
    op.add_column("kaspi_order_sync_state", sa.Column("last_fetched", sa.Integer(), nullable=True))
    op.add_column("kaspi_order_sync_state", sa.Column("last_inserted", sa.Integer(), nullable=True))
    op.add_column("kaspi_order_sync_state", sa.Column("last_updated", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("kaspi_order_sync_state", "last_updated")
    op.drop_column("kaspi_order_sync_state", "last_inserted")
    op.drop_column("kaspi_order_sync_state", "last_fetched")
    op.drop_column("kaspi_order_sync_state", "last_result")
    op.drop_column("kaspi_order_sync_state", "last_duration_ms")
    op.drop_column("kaspi_order_sync_state", "last_attempt_at")
