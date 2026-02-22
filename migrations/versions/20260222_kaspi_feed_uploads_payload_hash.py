"""kaspi feed uploads payload hash fields

Revision ID: 20260222_kaspi_feed_uploads_payload_hash
Revises: 20260301_kaspi_import_runs_polling
Create Date: 2026-02-22 00:00:00.000000
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "20260222_kaspi_feed_uploads_payload_hash"
down_revision = "20260301_kaspi_import_runs_polling"
branch_labels = None
depends_on = None


def upgrade() -> None:
	op.add_column(
		"kaspi_feed_uploads",
		sa.Column("payload_hash", sa.String(length=64), nullable=True),
	)
	op.add_column(
		"kaspi_feed_uploads",
		sa.Column("response_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
	)
	op.add_column(
		"kaspi_feed_uploads",
		sa.Column("next_attempt_at", sa.DateTime(), nullable=True),
	)
	op.create_index(
		"ix_kaspi_feed_uploads_payload_hash",
		"kaspi_feed_uploads",
		["payload_hash"],
	)


def downgrade() -> None:
	op.drop_index("ix_kaspi_feed_uploads_payload_hash", table_name="kaspi_feed_uploads")
	op.drop_column("kaspi_feed_uploads", "next_attempt_at")
	op.drop_column("kaspi_feed_uploads", "response_json")
	op.drop_column("kaspi_feed_uploads", "payload_hash")
