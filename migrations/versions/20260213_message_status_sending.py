"""Add sending to message_status enum

Revision ID: 20260213_message_status_sending
Revises: 20260212_campaign_processing_queue
Create Date: 2026-02-13
"""

from alembic import op

revision = "20260213_message_status_sending"
down_revision = "20260212_campaign_processing_queue"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("ALTER TYPE message_status ADD VALUE IF NOT EXISTS 'SENDING'")


def downgrade() -> None:
    # Postgres enums are not trivially reversible.
    pass
