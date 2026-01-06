"""kaspi: order status history unique

Revision ID: 29a2929fc59b
Revises: 2d43c3d56e28
Create Date: 2026-01-06 09:19:55.527176+00:00

"""
from collections.abc import Sequence
from typing import Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "29a2929fc59b"
down_revision: Union[str, Sequence[str], None] = "2d43c3d56e28"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_unique_constraint(
        "uq__order_status_history__order_status_changed",
        "order_status_history",
        ["order_id", "new_status", "changed_at"],
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint("uq__order_status_history__order_status_changed", "order_status_history", type_="unique")
