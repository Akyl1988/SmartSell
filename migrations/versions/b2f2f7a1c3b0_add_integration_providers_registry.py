"""Add integration providers registry tables

Revision ID: b2f2f7a1c3b0
Revises: 0c0c5c57a5b1
Create Date: 2025-12-26
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "b2f2f7a1c3b0"
down_revision = "0c0c5c57a5b1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "integration_providers",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True, nullable=False),
        sa.Column("domain", sa.String(length=64), nullable=False),
        sa.Column("provider", sa.String(length=128), nullable=False),
        sa.Column(
            "config_json",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=True,
        ),
        sa.Column(
            "capabilities",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=True,
        ),
        sa.Column("is_enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("updated_by_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.UniqueConstraint("domain", "provider", name=op.f("uq__integration_providers__domain_provider")),
    )
    op.create_index(
        op.f("ix__integration_providers__domain"),
        "integration_providers",
        ["domain"],
        unique=False,
    )
    op.create_index(
        "uq__integration_providers__domain_active",
        "integration_providers",
        ["domain"],
        unique=True,
        postgresql_where=sa.text("is_active = true"),
    )

    op.create_table(
        "integration_provider_events",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True, nullable=False),
        sa.Column("domain", sa.String(length=64), nullable=False),
        sa.Column("provider_from", sa.String(length=128), nullable=True),
        sa.Column("provider_to", sa.String(length=128), nullable=False),
        sa.Column("actor_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=False),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "meta_json",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=True,
        ),
    )
    op.create_index(
        op.f("ix__integration_provider_events__domain"),
        "integration_provider_events",
        ["domain"],
        unique=False,
    )
    op.create_index(
        op.f("ix__integration_provider_events__created_at"),
        "integration_provider_events",
        ["created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix__integration_provider_events__created_at"), table_name="integration_provider_events")
    op.drop_index(op.f("ix__integration_provider_events__domain"), table_name="integration_provider_events")
    op.drop_table("integration_provider_events")

    op.drop_index("uq__integration_providers__domain_active", table_name="integration_providers")
    op.drop_index(op.f("ix__integration_providers__domain"), table_name="integration_providers")
    op.drop_table("integration_providers")
