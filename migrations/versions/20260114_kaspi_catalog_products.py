"""feat(kaspi): add kaspi catalog products

Revision ID: 20260114_kaspi_catalog_products
Revises: 3a4e0c5f9c2b
Create Date: 2026-01-14 07:30:00.000000+00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "20260114_kaspi_catalog_products"
down_revision: Union[str, Sequence[str], None] = "e94854a3fe4b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "kaspi_catalog_products",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("company_id", sa.Integer(), sa.ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("offer_id", sa.String(length=128), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=True),
        sa.Column("sku", sa.String(length=128), nullable=True),
        sa.Column("price", sa.Numeric(18, 2), nullable=True),
        sa.Column("qty", sa.Integer(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("raw", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("company_id", "offer_id", name="uq_kaspi_catalog_products_company_offer"),
    )
    op.create_index("ix_kaspi_catalog_products_company_offer", "kaspi_catalog_products", ["company_id", "offer_id"])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_kaspi_catalog_products_company_offer", table_name="kaspi_catalog_products")
    op.drop_table("kaspi_catalog_products")
