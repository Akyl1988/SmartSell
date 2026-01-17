"""feat(kaspi): add public feed tokens

Revision ID: 20260117_kaspi_feed_pub_tokens
Revises: 20260117_kaspi_catalog_import
Create Date: 2026-01-17 15:30:00.000000+00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "20260117_kaspi_feed_pub_tokens"
down_revision: Union[str, Sequence[str], None] = "20260117_kaspi_catalog_import"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "kaspi_feed_public_tokens",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column(
            "company_id",
            sa.Integer(),
            sa.ForeignKey("companies.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("merchant_uid", sa.String(length=128), nullable=True, index=True),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.Column("last_used_at", sa.DateTime(), nullable=True),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.UniqueConstraint("token_hash", name="uq_kaspi_feed_public_tokens_token_hash"),
    )
    op.create_index(
        "ix_kaspi_feed_public_tokens_company",
        "kaspi_feed_public_tokens",
        ["company_id"],
    )
    op.create_index(
        "ix_kaspi_feed_public_tokens_merchant",
        "kaspi_feed_public_tokens",
        ["merchant_uid"],
    )
    op.create_index(
        "ix_kaspi_feed_public_tokens_hash",
        "kaspi_feed_public_tokens",
        ["token_hash"],
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_kaspi_feed_public_tokens_hash", table_name="kaspi_feed_public_tokens")
    op.drop_index("ix_kaspi_feed_public_tokens_merchant", table_name="kaspi_feed_public_tokens")
    op.drop_index("ix_kaspi_feed_public_tokens_company", table_name="kaspi_feed_public_tokens")
    op.drop_table("kaspi_feed_public_tokens")
