"""kaspi: unique order_items (order_id, sku)

Revision ID: 2d43c3d56e28
Revises: e3ee67c23527
Create Date: 2026-01-06 08:52:25.942187+00:00

"""
from collections.abc import Sequence
from typing import Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "2d43c3d56e28"
down_revision: Union[str, Sequence[str], None] = "e3ee67c23527"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_unique_constraint("uq__order_items__order_id_sku", "order_items", ["order_id", "sku"])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint("uq__order_items__order_id_sku", "order_items", type_="unique")
