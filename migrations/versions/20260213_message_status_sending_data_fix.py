"""Fix legacy uppercase SENDING statuses

Revision ID: 20260213_message_status_sending_data_fix
Revises: 20260213_message_status_sending_lowercase
Create Date: 2026-02-13
"""

from alembic import op

revision = "20260213_message_status_sending_data_fix"
down_revision = "20260213_message_status_sending_lowercase"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute(
            "UPDATE messages "
            "SET status = 'sending'::message_status "
            "WHERE status::text = 'SENDING'"
        )


def downgrade() -> None:
    # Postgres enums are not trivially reversible.
    pass
