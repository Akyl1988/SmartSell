"""feat(kaspi): add import run polling metadata

Revision ID: 20260301_kaspi_import_runs_polling
Revises: 20260222_kaspi_import_runs
Create Date: 2026-03-01
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20260301_kaspi_import_runs_polling"
down_revision: Union[str, Sequence[str], None] = "20260222_kaspi_import_runs"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("kaspi_import_runs", sa.Column("next_poll_at", sa.DateTime(), nullable=True))
    op.create_index(
        "ix_kaspi_import_runs_payload_hash",
        "kaspi_import_runs",
        ["company_id", "merchant_uid", "payload_hash"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_kaspi_import_runs_payload_hash", table_name="kaspi_import_runs")
    op.drop_column("kaspi_import_runs", "next_poll_at")
