"""feat(kaspi): add kaspi import runs

Revision ID: 20260222_kaspi_import_runs
Revises: 20260217_preorders_store_module
Create Date: 2026-02-22
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "20260222_kaspi_import_runs"
down_revision: Union[str, Sequence[str], None] = "20260217_kaspi_preorders_external_id"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "kaspi_import_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column(
            "company_id",
            sa.Integer(),
            sa.ForeignKey("companies.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("merchant_uid", sa.String(length=128), nullable=False),
        sa.Column("import_code", sa.String(length=64), nullable=False),
        sa.Column("kaspi_import_code", sa.String(length=128), nullable=True),
        sa.Column("status", sa.String(length=64), nullable=False, server_default=sa.text("'created'")),
        sa.Column("request_id", sa.String(length=128), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("payload_hash", sa.String(length=64), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "request_payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("status_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("result_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("last_checked_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_kaspi_import_runs_company", "kaspi_import_runs", ["company_id"], unique=False)
    op.create_index("ix_kaspi_import_runs_merchant_uid", "kaspi_import_runs", ["merchant_uid"], unique=False)
    op.create_index("ix_kaspi_import_runs_import_code", "kaspi_import_runs", ["import_code"], unique=False)
    op.create_index("ix_kaspi_import_runs_kaspi_code", "kaspi_import_runs", ["kaspi_import_code"], unique=False)
    op.create_index("ix_kaspi_import_runs_request_id", "kaspi_import_runs", ["request_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_kaspi_import_runs_request_id", table_name="kaspi_import_runs")
    op.drop_index("ix_kaspi_import_runs_kaspi_code", table_name="kaspi_import_runs")
    op.drop_index("ix_kaspi_import_runs_import_code", table_name="kaspi_import_runs")
    op.drop_index("ix_kaspi_import_runs_merchant_uid", table_name="kaspi_import_runs")
    op.drop_index("ix_kaspi_import_runs_company", table_name="kaspi_import_runs")
    op.drop_table("kaspi_import_runs")
