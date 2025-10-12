"""merge heads

Revision ID: 37d99ac316e7
Revises: 4b9b6b9d1f30, b1f1a6b0d3a1
Create Date: 2025-10-10 16:57:51.024087+00:00

"""
from collections.abc import Sequence
from typing import Union

# revision identifiers, used by Alembic.
revision: str = "37d99ac316e7"
down_revision: Union[str, Sequence[str], None] = ("4b9b6b9d1f30", "b1f1a6b0d3a1")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
