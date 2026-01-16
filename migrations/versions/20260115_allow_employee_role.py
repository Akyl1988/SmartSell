"""fix(auth): allow employee role in users

Revision ID: 20260115_allow_employee_role
Revises: 20260115_invite_reset
Create Date: 2026-01-15 00:00:01.000000+00:00

"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260115_allow_employee_role"
down_revision = "20260115_invite_reset"
branch_labels = None
depends_on = None

NEW_ALLOWED_ROLES = (
    "admin",
    "employee",
    "manager",
    "storekeeper",
    "analyst",
    "platform_admin",
)

OLD_ALLOWED_ROLES = (
    "admin",
    "manager",
    "storekeeper",
    "analyst",
    "platform_admin",
)


def upgrade() -> None:
    op.drop_constraint(op.f("ck__users__ck_user_role_allowed"), "users", type_="check")
    op.create_check_constraint(
        op.f("ck__users__ck_user_role_allowed"),
        "users",
        sa.text("role IN ('" + "','".join(NEW_ALLOWED_ROLES) + "')"),
    )


def downgrade() -> None:
    op.drop_constraint(op.f("ck__users__ck_user_role_allowed"), "users", type_="check")
    op.create_check_constraint(
        op.f("ck__users__ck_user_role_allowed"),
        "users",
        sa.text("role IN ('" + "','".join(OLD_ALLOWED_ROLES) + "')"),
    )
