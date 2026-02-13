"""Allow platform_manager role in users.role check constraint.

Revision ID: 20260213_user_role_platform_manager
Revises: 20260213_message_status_sending_data_fix
Create Date: 2026-02-13
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision = "20260213_user_role_platform_manager"
down_revision = "20260213_message_status_sending_data_fix"
branch_labels = None
depends_on = None


def _quote_ident(name: str) -> str:
    escaped = name.replace('"', '""')
    return f'"{escaped}"'


def _drop_role_constraints(conn, table_ref: str) -> None:
    op.execute(f"ALTER TABLE {table_ref} DROP CONSTRAINT IF EXISTS ck_user_role_allowed")
    rows = conn.execute(
        text(
            """
            SELECT con.conname
              FROM pg_constraint con
              JOIN pg_class rel ON rel.oid = con.conrelid
              JOIN pg_namespace nsp ON nsp.oid = rel.relnamespace
              JOIN unnest(con.conkey) AS cols(attnum) ON TRUE
              JOIN pg_attribute att ON att.attrelid = rel.oid AND att.attnum = cols.attnum
             WHERE rel.relname = 'users'
               AND nsp.nspname = current_schema()
               AND con.contype = 'c'
               AND att.attname = 'role'
            """
        )
    ).fetchall()
    for row in rows:
        conname = row[0]
        op.execute(f"ALTER TABLE {table_ref} DROP CONSTRAINT {_quote_ident(conname)}")


def upgrade() -> None:
    ctx = op.get_context()
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    if getattr(ctx, "as_sql", False):
        table_ref = "users"
        op.execute(f"ALTER TABLE {table_ref} DROP CONSTRAINT IF EXISTS ck_user_role_allowed")
        op.execute(
            "ALTER TABLE "
            + table_ref
            + " ADD CONSTRAINT ck_user_role_allowed "
            + "CHECK (role IN ('admin','employee','manager','storekeeper','analyst','platform_admin','platform_manager'))"
        )
        return

    schema = bind.execute(text("select current_schema()")).scalar()
    table_ref = f"{_quote_ident(schema)}.{_quote_ident('users')}" if schema else "users"

    _drop_role_constraints(bind, table_ref)

    op.execute(
        "ALTER TABLE "
        + table_ref
        + " ADD CONSTRAINT ck_user_role_allowed "
        + "CHECK (role IN ('admin','employee','manager','storekeeper','analyst','platform_admin','platform_manager'))"
    )


def downgrade() -> None:
    ctx = op.get_context()
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    if getattr(ctx, "as_sql", False):
        table_ref = "users"
        op.execute(f"ALTER TABLE {table_ref} DROP CONSTRAINT IF EXISTS ck_user_role_allowed")
        op.execute(
            "ALTER TABLE "
            + table_ref
            + " ADD CONSTRAINT ck_user_role_allowed "
            + "CHECK (role IN ('admin','employee','manager','storekeeper','analyst','platform_admin'))"
        )
        return

    schema = bind.execute(text("select current_schema()")).scalar()
    table_ref = f"{_quote_ident(schema)}.{_quote_ident('users')}" if schema else "users"

    _drop_role_constraints(bind, table_ref)

    op.execute(
        "ALTER TABLE "
        + table_ref
        + " ADD CONSTRAINT ck_user_role_allowed "
        + "CHECK (role IN ('admin','employee','manager','storekeeper','analyst','platform_admin'))"
    )
