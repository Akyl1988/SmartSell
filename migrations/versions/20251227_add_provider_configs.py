"""add integration provider configs

Revision ID: 20251227_add_provider_configs
Revises: b2f2f7a1c3b0
Create Date: 2025-12-27 00:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "20251227_add_provider_configs"
down_revision = "b2f2f7a1c3b0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "integration_provider_configs",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("domain", sa.String(length=64), nullable=False),
        sa.Column("provider", sa.String(length=128), nullable=False),
        sa.Column("config_encrypted", sa.LargeBinary(), nullable=False),
        sa.Column("key_id", sa.String(length=64), nullable=False, server_default="master"),
        sa.Column("meta_json", sa.dialects.postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "domain", "provider", name="uq__integration_provider_configs__domain_provider"
        ),
    )
    op.create_index(
        "ix_integration_provider_configs_domain", "integration_provider_configs", ["domain"]
    )


def downgrade() -> None:
    op.drop_index(
        "ix_integration_provider_configs_domain", table_name="integration_provider_configs"
    )
    op.drop_table("integration_provider_configs")
