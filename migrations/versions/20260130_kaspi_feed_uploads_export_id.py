"""feat(kaspi): add export_id to feed uploads

Revision ID: 20260130_kaspi_feed_uploads_export_id
Revises: 20260130_kaspi_goods_imports_filename
Create Date: 2026-01-30 00:00:00.000000+00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "20260130_kaspi_feed_uploads_export_id"
down_revision: Union[str, Sequence[str], None] = "20260130_kaspi_goods_imports_filename"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("kaspi_feed_uploads", sa.Column("export_id", sa.Integer(), nullable=True))
    op.create_index("ix_kaspi_feed_uploads_export_id", "kaspi_feed_uploads", ["export_id"])


def downgrade() -> None:
    op.drop_index("ix_kaspi_feed_uploads_export_id", table_name="kaspi_feed_uploads")
    op.drop_column("kaspi_feed_uploads", "export_id")
