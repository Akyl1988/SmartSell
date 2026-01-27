"""alter users.hashed_password to text

Revision ID: 20260127_users_hashed_password_text
Revises: 20260122_kaspi_feed_uploads
Create Date: 2026-01-27
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "20260127_users_hashed_password_text"
down_revision = "20260122_kaspi_feed_uploads"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "users",
        "hashed_password",
        existing_type=sa.VARCHAR(length=255),
        type_=sa.Text(),
        existing_nullable=False,
    )


def downgrade() -> None:
    # NOTE: downgrade may fail if any values exceed 255 chars.
    op.alter_column(
        "users",
        "hashed_password",
        existing_type=sa.Text(),
        type_=sa.VARCHAR(length=255),
        existing_nullable=False,
    )
