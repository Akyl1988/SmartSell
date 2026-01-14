"""feat(kaspi): add kaspi feed exports

Revision ID: 20260114_kaspi_feed_exports
Revises: 20260114_kaspi_catalog_products
Create Date: 2026-01-14 10:00:00.000000+00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "20260114_kaspi_feed_exports"
down_revision: Union[str, Sequence[str], None] = "20260114_kaspi_catalog_products"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "kaspi_feed_exports",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("company_id", sa.Integer(), sa.ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("kind", sa.String(length=50), nullable=False),
        sa.Column("format", sa.String(length=50), nullable=False),
        sa.Column("status", sa.String(length=50), nullable=False, server_default=sa.text("'generated'")),
        sa.Column("checksum", sa.String(length=64), nullable=False),
        sa.Column("payload_text", sa.Text(), nullable=False),
        sa.Column("stats_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("company_id", "kind", "checksum", name="uq_kaspi_feed_exports_company_kind_checksum"),
    )
    op.create_index("ix_kaspi_feed_exports_company_created", "kaspi_feed_exports", ["company_id", "created_at"])
    op.create_index("ix_kaspi_feed_exports_company_kind", "kaspi_feed_exports", ["company_id", "kind"])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_kaspi_feed_exports_company_kind", table_name="kaspi_feed_exports")
    op.drop_index("ix_kaspi_feed_exports_company_created", table_name="kaspi_feed_exports")
    op.drop_table("kaspi_feed_exports")
