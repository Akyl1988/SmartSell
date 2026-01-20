"""feat(kaspi): extend goods imports

Revision ID: 20260120_kaspi_goods_imports_ext
Revises: 20260117_kaspi_feed_pub_tokens
Create Date: 2026-01-20 10:00:00.000000+00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "20260120_kaspi_goods_imports_ext"
down_revision: Union[str, Sequence[str], None] = "20260117_kaspi_feed_pub_tokens"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("kaspi_goods_imports", sa.Column("merchant_uid", sa.String(length=128), nullable=True))
    op.add_column(
        "kaspi_goods_imports",
        sa.Column(
            "request_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.add_column("kaspi_goods_imports", sa.Column("status_json", postgresql.JSONB(astext_type=sa.Text())))
    op.add_column("kaspi_goods_imports", sa.Column("result_json", postgresql.JSONB(astext_type=sa.Text())))
    op.add_column("kaspi_goods_imports", sa.Column("error_code", sa.String(length=64), nullable=True))
    op.add_column("kaspi_goods_imports", sa.Column("error_message", sa.Text(), nullable=True))
    op.add_column("kaspi_goods_imports", sa.Column("last_checked_at", sa.DateTime(), nullable=True))
    op.add_column("kaspi_goods_imports", sa.Column("revoked_at", sa.DateTime(), nullable=True))

    op.create_index("ix_kaspi_goods_imports_merchant_uid", "kaspi_goods_imports", ["merchant_uid"])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_kaspi_goods_imports_merchant_uid", table_name="kaspi_goods_imports")

    op.drop_column("kaspi_goods_imports", "revoked_at")
    op.drop_column("kaspi_goods_imports", "last_checked_at")
    op.drop_column("kaspi_goods_imports", "error_message")
    op.drop_column("kaspi_goods_imports", "error_code")
    op.drop_column("kaspi_goods_imports", "result_json")
    op.drop_column("kaspi_goods_imports", "status_json")
    op.drop_column("kaspi_goods_imports", "request_json")
    op.drop_column("kaspi_goods_imports", "merchant_uid")
