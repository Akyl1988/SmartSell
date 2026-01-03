"""add deleted_at and ended_at to subscriptions (offline-safe)

Revision ID: 20251228_subs_deleted_at
Revises: 20251228_active_sub_uniq
Create Date: 2025-12-28
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa  # kept for alembic context / consistency

# revision identifiers, used by Alembic.
revision = "20251228_subs_deleted_at"
down_revision = "20251228_active_sub_uniq"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Offline-safe: no inspect; additive DDL with IF NOT EXISTS
    op.execute('ALTER TABLE IF EXISTS "subscriptions" ADD COLUMN IF NOT EXISTS "deleted_at" TIMESTAMP WITHOUT TIME ZONE')
    op.execute('ALTER TABLE IF EXISTS "subscriptions" ADD COLUMN IF NOT EXISTS "ended_at" TIMESTAMP WITH TIME ZONE')
    op.execute('CREATE INDEX IF NOT EXISTS "ix_subscriptions_deleted_at" ON "subscriptions" ("deleted_at")')


def downgrade() -> None:
    # Offline-safe teardown
    op.execute('DROP INDEX IF EXISTS "ix_subscriptions_deleted_at"')
    op.execute('ALTER TABLE IF EXISTS "subscriptions" DROP COLUMN IF EXISTS "ended_at"')
    op.execute('ALTER TABLE IF EXISTS "subscriptions" DROP COLUMN IF EXISTS "deleted_at"')
