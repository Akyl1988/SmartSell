"""Add kaspi feed upload jobs

Revision ID: 20260130_kaspi_feed_uploads
Revises: 20260129_subscription_enforcement_fields
Create Date: 2026-01-30
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "20260130_kaspi_feed_uploads"
down_revision: Union[str, Sequence[str], None] = "20260129_integration_events"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "kaspi_feed_uploads",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("company_id", sa.Integer(), sa.ForeignKey("companies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("merchant_uid", sa.String(length=128), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=True),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=64), nullable=False, server_default=sa.text("'created'")),
        sa.Column("import_code", sa.String(length=128), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("last_error_code", sa.String(length=64), nullable=True),
        sa.Column("last_error_message", sa.Text(), nullable=True),
        sa.Column("request_id", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
    )

    op.create_index("ix_kaspi_feed_uploads_company_id", "kaspi_feed_uploads", ["company_id"], unique=False)
    op.create_index("ix_kaspi_feed_uploads_merchant_uid", "kaspi_feed_uploads", ["merchant_uid"], unique=False)
    op.create_index("ix_kaspi_feed_uploads_import_code", "kaspi_feed_uploads", ["import_code"], unique=False)
    op.create_index("ix_kaspi_feed_uploads_request_id", "kaspi_feed_uploads", ["request_id"], unique=False)
    op.create_index(
        "ux_kaspi_feed_uploads_company_request_id",
        "kaspi_feed_uploads",
        ["company_id", "request_id"],
        unique=True,
        postgresql_where=sa.text("request_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ux_kaspi_feed_uploads_company_request_id", table_name="kaspi_feed_uploads")
    op.drop_index("ix_kaspi_feed_uploads_request_id", table_name="kaspi_feed_uploads")
    op.drop_index("ix_kaspi_feed_uploads_import_code", table_name="kaspi_feed_uploads")
    op.drop_index("ix_kaspi_feed_uploads_merchant_uid", table_name="kaspi_feed_uploads")
    op.drop_index("ix_kaspi_feed_uploads_company_id", table_name="kaspi_feed_uploads")
    op.drop_table("kaspi_feed_uploads")
