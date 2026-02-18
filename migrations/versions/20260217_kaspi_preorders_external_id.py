"""Add external id/source to preorders.

Revision ID: 20260217_kaspi_preorders_external_id
Revises: 20260217_preorders_fulfillment_order
Create Date: 2026-02-17
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260217_kaspi_preorders_external_id"
down_revision = "20260217_preorders_fulfillment_order"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("preorders", sa.Column("source", sa.String(length=32), nullable=True))
    op.add_column("preorders", sa.Column("external_id", sa.String(length=128), nullable=True))

    op.create_index("ix_preorders_source", "preorders", ["source"], unique=False)
    op.create_index("ix_preorders_external_id", "preorders", ["external_id"], unique=False)
    op.create_unique_constraint(
        "uq_preorders_company_source_external_id",
        "preorders",
        ["company_id", "source", "external_id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_preorders_company_source_external_id",
        "preorders",
        type_="unique",
    )
    op.drop_index("ix_preorders_external_id", table_name="preorders")
    op.drop_index("ix_preorders_source", table_name="preorders")

    op.drop_column("preorders", "external_id")
    op.drop_column("preorders", "source")
