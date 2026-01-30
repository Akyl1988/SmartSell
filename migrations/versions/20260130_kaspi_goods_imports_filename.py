"""feat(kaspi): add filename to goods imports

Revision ID: 20260130_kaspi_goods_imports_filename
Revises: 20260130_kaspi_feed_uploads
Create Date: 2026-01-30 00:00:00.000000+00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "20260130_kaspi_goods_imports_filename"
down_revision: Union[str, Sequence[str], None] = "20260130_kaspi_feed_uploads"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("kaspi_goods_imports", sa.Column("filename", sa.String(length=256), nullable=True))


def downgrade() -> None:
    op.drop_column("kaspi_goods_imports", "filename")
