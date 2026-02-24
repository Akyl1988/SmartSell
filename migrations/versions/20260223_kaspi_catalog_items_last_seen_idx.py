"""feat(kaspi): add latest-first index for catalog items

Revision ID: 20260223_kaspi_catalog_items_last_seen_idx
Revises: 20260223_kaspi_catalog_items
Create Date: 2026-02-23
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20260223_kaspi_catalog_items_last_seen_idx"
down_revision: Union[str, Sequence[str], None] = "20260223_kaspi_catalog_items"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(
        "ix_kaspi_catalog_items_company_merchant_last_seen",
        "kaspi_catalog_items",
        ["company_id", "merchant_uid", sa.text("last_seen_at DESC")],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_kaspi_catalog_items_company_merchant_last_seen",
        table_name="kaspi_catalog_items",
    )
