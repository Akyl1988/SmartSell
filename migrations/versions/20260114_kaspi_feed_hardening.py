"""feat(kaspi): add retry and diagnostics fields to feed exports

Revision ID: 20260114_kaspi_feed_hardening
Revises: 20260114_kaspi_feed_exports
Create Date: 2026-01-14 12:45:00.000000+00:00

"""
from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20260114_kaspi_feed_hardening"
down_revision: Union[str, Sequence[str], None] = "20260114_kaspi_feed_exports"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add retry and diagnostics fields to kaspi_feed_exports."""
    op.add_column("kaspi_feed_exports", sa.Column("attempts", sa.Integer(), nullable=False, server_default=sa.text("0")))
    op.add_column("kaspi_feed_exports", sa.Column("last_attempt_at", sa.DateTime(), nullable=True))
    op.add_column("kaspi_feed_exports", sa.Column("uploaded_at", sa.DateTime(), nullable=True))
    op.add_column("kaspi_feed_exports", sa.Column("duration_ms", sa.Integer(), nullable=True))


def downgrade() -> None:
    """Remove retry and diagnostics fields from kaspi_feed_exports."""
    op.drop_column("kaspi_feed_exports", "duration_ms")
    op.drop_column("kaspi_feed_exports", "uploaded_at")
    op.drop_column("kaspi_feed_exports", "last_attempt_at")
    op.drop_column("kaspi_feed_exports", "attempts")
