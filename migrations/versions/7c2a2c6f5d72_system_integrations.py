"""Add system integrations registry tables

Revision ID: 7c2a2c6f5d72
Revises: 4fa113a3445f
Create Date: 2025-12-26
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "7c2a2c6f5d72"
down_revision = "4fa113a3445f"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "system_integrations",
        sa.Column("id", sa.BigInteger(), nullable=False, autoincrement=True),
        sa.Column("domain", sa.String(length=64), nullable=False),
        sa.Column("provider", sa.String(length=128), nullable=False),
        sa.Column(
            "config_encrypted",
            sa.LargeBinary().with_variant(postgresql.BYTEA(), "postgresql"),
            nullable=False,
        ),
        sa.Column("is_enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column(
            "capabilities",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=True,
        ),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk__system_integrations")),
        sa.UniqueConstraint("domain", "provider", name=op.f("uq__system_integrations__domain_provider")),
        sa.CheckConstraint("version > 0", name=op.f("ck__system_integrations__version_pos")),
    )
    op.create_index(
        op.f("ix__system_integrations__domain"),
        "system_integrations",
        ["domain"],
        unique=False,
    )

    op.create_table(
        "system_active_providers",
        sa.Column("domain", sa.String(length=64), nullable=False),
        sa.Column("provider", sa.String(length=128), nullable=False),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("domain", name=op.f("pk__system_active_providers")),
        sa.CheckConstraint("version > 0", name=op.f("ck__system_active_providers__version_pos")),
    )


def downgrade() -> None:
    op.drop_table("system_active_providers")
    op.drop_index(op.f("ix__system_integrations__domain"), table_name="system_integrations")
    op.drop_table("system_integrations")
