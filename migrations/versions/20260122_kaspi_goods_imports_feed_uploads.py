"""feat(kaspi): feed upload fields for goods imports

Revision ID: 20260122_kaspi_feed_uploads
Revises: 20260121_kaspi_mc_sessions
Create Date: 2026-01-22 10:00:00.000000+00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "20260122_kaspi_feed_uploads"
down_revision: Union[str, Sequence[str], None] = "20260121_kaspi_mc_sessions"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("kaspi_goods_imports", sa.Column("source", sa.String(length=32), nullable=True))
    op.add_column("kaspi_goods_imports", sa.Column("comment", sa.Text(), nullable=True))
    op.add_column("kaspi_goods_imports", sa.Column("last_error_at", sa.DateTime(), nullable=True))
    op.add_column("kaspi_goods_imports", sa.Column("raw_response", sa.Text(), nullable=True))

    op.create_index("ix_kaspi_goods_imports_source", "kaspi_goods_imports", ["source"])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_kaspi_goods_imports_source", table_name="kaspi_goods_imports")

    op.drop_column("kaspi_goods_imports", "raw_response")
    op.drop_column("kaspi_goods_imports", "last_error_at")
    op.drop_column("kaspi_goods_imports", "comment")
    op.drop_column("kaspi_goods_imports", "source")
