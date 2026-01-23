"""feat(kaspi): add kaspi mc sessions

Revision ID: 20260121_kaspi_mc_sessions
Revises: 20260120_kaspi_goods_imports_ext
Create Date: 2026-01-21 10:00:00.000000+00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "20260121_kaspi_mc_sessions"
down_revision: Union[str, Sequence[str], None] = "20260120_kaspi_goods_imports_ext"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "kaspi_mc_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column(
            "company_id",
            sa.Integer(),
            sa.ForeignKey("companies.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("merchant_uid", sa.String(length=128), nullable=False, index=True),
        sa.Column("cookies_ciphertext", postgresql.BYTEA(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("last_used_at", sa.DateTime(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.UniqueConstraint("company_id", "merchant_uid", name="uq_kaspi_mc_sessions_company_merchant"),
    )
    op.create_index("ix_kaspi_mc_sessions_company_id", "kaspi_mc_sessions", ["company_id"])
    op.create_index("ix_kaspi_mc_sessions_merchant_uid", "kaspi_mc_sessions", ["merchant_uid"])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_kaspi_mc_sessions_merchant_uid", table_name="kaspi_mc_sessions")
    op.drop_index("ix_kaspi_mc_sessions_company_id", table_name="kaspi_mc_sessions")
    op.drop_table("kaspi_mc_sessions")