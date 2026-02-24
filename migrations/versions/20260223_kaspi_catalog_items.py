"""feat(kaspi): add catalog items from orders

Revision ID: 20260223_kaspi_catalog_items
Revises: 20260222_kaspi_feed_uploads_payload_hash
Create Date: 2026-02-23
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "20260223_kaspi_catalog_items"
down_revision: Union[str, Sequence[str], None] = "20260222_kaspi_feed_uploads_payload_hash"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "kaspi_catalog_items",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("company_id", sa.Integer(), sa.ForeignKey("companies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("merchant_uid", sa.String(length=128), nullable=False),
        sa.Column("sku", sa.String(length=128), nullable=False),
        sa.Column("offer_code", sa.String(length=128), nullable=True),
        sa.Column("product_code", sa.String(length=128), nullable=True),
        sa.Column("last_seen_name", sa.String(length=255), nullable=True),
        sa.Column("last_seen_price", sa.Numeric(18, 2), nullable=True),
        sa.Column("last_seen_qty", sa.Integer(), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(), nullable=True),
        sa.Column("raw", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("company_id", "merchant_uid", "sku", name="uq_kaspi_catalog_items_company_merchant_sku"),
    )
    op.create_index(
        "ix_kaspi_catalog_items_company_merchant",
        "kaspi_catalog_items",
        ["company_id", "merchant_uid"],
    )
    op.create_index(
        "ix_kaspi_catalog_items_company_sku",
        "kaspi_catalog_items",
        ["company_id", "sku"],
    )


def downgrade() -> None:
    op.drop_index("ix_kaspi_catalog_items_company_sku", table_name="kaspi_catalog_items")
    op.drop_index("ix_kaspi_catalog_items_company_merchant", table_name="kaspi_catalog_items")
    op.drop_table("kaspi_catalog_items")
