"""Fix: allow platform_admin user role

Revision ID: 0c0c5c57a5b1
Revises: 7c2a2c6f5d72
Create Date: 2025-12-26
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0c0c5c57a5b1"
down_revision = "7c2a2c6f5d72"
branch_labels = None
depends_on = None


NEW_ALLOWED_ROLES = (
    "admin",
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
