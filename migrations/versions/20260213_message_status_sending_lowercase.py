"""Add lowercase sending to message_status enum

Revision ID: 20260213_message_status_sending_lowercase
Revises: 20260213_message_status_sending
Create Date: 2026-02-13
"""

from alembic import op
from sqlalchemy import text

revision = "20260213_message_status_sending_lowercase"
down_revision = "20260213_message_status_sending"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        ctx = op.get_context()
        if getattr(ctx, "as_sql", False):
            op.execute("ALTER TYPE public.message_status ADD VALUE IF NOT EXISTS 'sending'")
            return
        engine = bind.engine
        conn = engine.connect()
        try:
            conn = conn.execution_options(isolation_level="AUTOCOMMIT")
            exists = conn.execute(
                text(
                    "SELECT 1 "
                    "FROM pg_type t "
                    "JOIN pg_namespace n ON n.oid = t.typnamespace "
                    "WHERE t.typname = 'message_status' AND n.nspname = 'public'"
                )
            ).scalar()
            if exists:
                conn.execute(text("ALTER TYPE public.message_status ADD VALUE IF NOT EXISTS 'sending'"))
        finally:
            try:
                conn.close()
            except Exception:
                pass


def downgrade() -> None:
    # Postgres enums are not trivially reversible.
    pass
