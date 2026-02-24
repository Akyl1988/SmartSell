"""campaign add created_at updated_at

Revision ID: d438caa3675c
Revises: 20260223_kaspi_catalog_items_last_seen_idx
Create Date: 2026-02-24 17:53:53.338256+00:00

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "d438caa3675c"
down_revision: Union[str, Sequence[str], None] = "20260223_kaspi_catalog_items_last_seen_idx"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "campaigns",
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.add_column(
        "campaigns",
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.add_column(
        "messages",
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.add_column(
        "messages",
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("messages", "updated_at")
    op.drop_column("messages", "created_at")
    op.drop_column("campaigns", "updated_at")
    op.drop_column("campaigns", "created_at")
