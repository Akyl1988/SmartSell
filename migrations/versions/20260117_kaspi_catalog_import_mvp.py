"""feat(kaspi): add catalog import batches/rows and kaspi offers

Revision ID: 20260117_kaspi_catalog_import
Revises: 20260117_kaspi_goods_imports
Create Date: 2026-01-17 12:00:00.000000+00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "20260117_kaspi_catalog_import"
down_revision: Union[str, Sequence[str], None] = "20260117_kaspi_goods_imports"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "catalog_import_batches",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column(
            "company_id",
            sa.Integer(),
            sa.ForeignKey("companies.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("source", sa.String(length=32), nullable=False, server_default=sa.text("'kaspi'")),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default=sa.text("'PENDING'")),
        sa.Column("merchant_uid", sa.String(length=128), nullable=True),
        sa.Column("rows_total", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("rows_ok", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("rows_failed", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("error_summary", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_catalog_import_batches_company", "catalog_import_batches", ["company_id"])
    op.create_index("ix_catalog_import_batches_hash", "catalog_import_batches", ["content_hash"])

    op.create_table(
        "catalog_import_rows",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column(
            "batch_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("catalog_import_batches.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "company_id",
            sa.Integer(),
            sa.ForeignKey("companies.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("row_num", sa.Integer(), nullable=False),
        sa.Column(
            "raw",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("sku", sa.String(length=128), nullable=True),
        sa.Column("master_sku", sa.String(length=128), nullable=True),
        sa.Column("title", sa.String(length=255), nullable=True),
        sa.Column("price", sa.Integer(), nullable=True),
        sa.Column("old_price", sa.Integer(), nullable=True),
        sa.Column("stock_count", sa.Integer(), nullable=True),
        sa.Column("pre_order", sa.Boolean(), nullable=True),
        sa.Column("stock_specified", sa.Boolean(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
    )
    op.create_index("ix_catalog_import_rows_batch", "catalog_import_rows", ["batch_id"])
    op.create_index("ix_catalog_import_rows_company", "catalog_import_rows", ["company_id"])

    op.create_table(
        "kaspi_offers",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column(
            "company_id",
            sa.Integer(),
            sa.ForeignKey("companies.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("merchant_uid", sa.String(length=128), nullable=False),
        sa.Column("sku", sa.String(length=128), nullable=False),
        sa.Column("master_sku", sa.String(length=128), nullable=True),
        sa.Column("title", sa.String(length=255), nullable=True),
        sa.Column("price", sa.Numeric(18, 2), nullable=True),
        sa.Column("old_price", sa.Numeric(18, 2), nullable=True),
        sa.Column("stock_count", sa.Integer(), nullable=True),
        sa.Column("pre_order", sa.Boolean(), nullable=True),
        sa.Column("stock_specified", sa.Boolean(), nullable=True),
        sa.Column(
            "raw",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("company_id", "merchant_uid", "sku", name="uq_kaspi_offers_company_merchant_sku"),
    )
    op.create_index("ix_kaspi_offers_company", "kaspi_offers", ["company_id"])
    op.create_index("ix_kaspi_offers_company_sku", "kaspi_offers", ["company_id", "sku"])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_kaspi_offers_company_sku", table_name="kaspi_offers")
    op.drop_index("ix_kaspi_offers_company", table_name="kaspi_offers")
    op.drop_table("kaspi_offers")

    op.drop_index("ix_catalog_import_rows_company", table_name="catalog_import_rows")
    op.drop_index("ix_catalog_import_rows_batch", table_name="catalog_import_rows")
    op.drop_table("catalog_import_rows")

    op.drop_index("ix_catalog_import_batches_hash", table_name="catalog_import_batches")
    op.drop_index("ix_catalog_import_batches_company", table_name="catalog_import_batches")
    op.drop_table("catalog_import_batches")
