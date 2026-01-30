"""feat(kaspi): add goods import official columns

Revision ID: 20260130_kaspi_goods_imports_official_columns
Revises: 20260130_kaspi_feed_uploads_last_attempt_at
Create Date: 2026-01-30 00:00:00.000000+00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "20260130_kaspi_goods_imports_official_columns"
down_revision: Union[str, Sequence[str], None] = "20260130_kaspi_feed_uploads_last_attempt_at"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("kaspi_goods_imports", sa.Column("payload_hash", sa.String(length=64), nullable=True))
    op.add_column(
        "kaspi_goods_imports",
        sa.Column("attempts", sa.Integer(), nullable=False, server_default=sa.text("0")),
    )
    op.add_column(
        "kaspi_goods_imports",
        sa.Column("raw_status_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.create_index("ix_kaspi_goods_imports_payload_hash", "kaspi_goods_imports", ["payload_hash"])


def downgrade() -> None:
    op.drop_index("ix_kaspi_goods_imports_payload_hash", table_name="kaspi_goods_imports")
    op.drop_column("kaspi_goods_imports", "raw_status_json")
    op.drop_column("kaspi_goods_imports", "attempts")
    op.drop_column("kaspi_goods_imports", "payload_hash")
