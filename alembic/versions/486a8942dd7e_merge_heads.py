"""merge heads

Revision ID: 486a8942dd7e
Revises: 20251003_update_payments_customer_fk, 37880826cca6
Create Date: 2025-10-06 09:24:24.022806+00:00

"""
from collections.abc import Sequence
from typing import Union

# revision identifiers, used by Alembic.
revision: str = "486a8942dd7e"
down_revision: Union[str, Sequence[str], None] = (
    "20251003_update_payments_customer_fk",
    "37880826cca6",
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
