"""enforce single active subscription per company

Revision ID: 20251228_active_subscription_uniqueness
Revises: 20251227_add_provider_configs
Create Date: 2025-12-28 00:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "20251228_active_subscription_uniqueness"
down_revision = "20251227_add_provider_configs"
branch_labels = None
depends_on = None


ACTIVE_STATES = ("active", "trial", "overdue", "paused")


def upgrade() -> None:
    condition = sa.text(
        "status IN ('active','trial','overdue','paused') AND deleted_at IS NULL"
    )
    op.create_index(
        "uq_subscription_company_active_states",
        "subscriptions",
        ["company_id"],
        unique=True,
        postgresql_where=condition,
    )


def downgrade() -> None:
    op.drop_index("uq_subscription_company_active_states", table_name="subscriptions")
