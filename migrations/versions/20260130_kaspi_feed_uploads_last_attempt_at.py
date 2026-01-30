"""feat(kaspi): add last_attempt_at to feed uploads

Revision ID: 20260130_kaspi_feed_uploads_last_attempt_at
Revises: 20260130_kaspi_feed_uploads_export_id
Create Date: 2026-01-30 00:00:00.000000+00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "20260130_kaspi_feed_uploads_last_attempt_at"
down_revision: Union[str, Sequence[str], None] = "20260130_kaspi_feed_uploads_export_id"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("kaspi_feed_uploads", sa.Column("last_attempt_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column("kaspi_feed_uploads", "last_attempt_at")
