"""idempotency keys table

Revision ID: 20260205_idempotency_keys
Revises: 20260203_subscription_overrides
Create Date: 2026-02-05
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "20260205_idempotency_keys"
down_revision = "20260203_subscription_overrides"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "idempotency_keys",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("key", sa.String(length=200), nullable=False),
        sa.Column("status_code", sa.Integer(), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        schema="public",
    )
    op.create_index(
        "ix__idempotency_keys__company_id",
        "idempotency_keys",
        ["company_id"],
        schema="public",
    )
    op.create_index(
        "ix__idempotency_keys__expires_at",
        "idempotency_keys",
        ["expires_at"],
        schema="public",
    )
    op.create_unique_constraint(
        "uq__idempotency_keys__company_id__key",
        "idempotency_keys",
        ["company_id", "key"],
        schema="public",
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq__idempotency_keys__company_id__key",
        "idempotency_keys",
        type_="unique",
        schema="public",
    )
    op.drop_index(
        "ix__idempotency_keys__expires_at",
        table_name="idempotency_keys",
        schema="public",
    )
    op.drop_index(
        "ix__idempotency_keys__company_id",
        table_name="idempotency_keys",
        schema="public",
    )
    op.drop_table("idempotency_keys", schema="public")
