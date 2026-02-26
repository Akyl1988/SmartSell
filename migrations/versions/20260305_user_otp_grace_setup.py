"""Add OTP grace and setup flag to users.

Revision ID: 20260305_user_otp_grace_setup
Revises: 20260301_kaspi_import_runs_polling
Create Date: 2026-03-05
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260305_user_otp_grace_setup"
down_revision = "d438caa3675c"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("otp_grace_until", sa.DateTime(), nullable=True))
    op.add_column(
        "users",
        sa.Column("otp_setup_required", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )
    op.create_index("ix_users_otp_grace_until", "users", ["otp_grace_until"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_users_otp_grace_until", table_name="users")
    op.drop_column("users", "otp_setup_required")
    op.drop_column("users", "otp_grace_until")
