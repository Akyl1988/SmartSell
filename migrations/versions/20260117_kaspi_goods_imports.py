"""feat(kaspi): add kaspi goods imports

Revision ID: 20260117_kaspi_goods_imports
Revises: 20260115_allow_employee_role
Create Date: 2026-01-17 10:00:00.000000+00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "20260117_kaspi_goods_imports"
down_revision: Union[str, Sequence[str], None] = "20260115_allow_employee_role"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "kaspi_goods_imports",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column(
            "company_id",
            sa.Integer(),
            sa.ForeignKey("companies.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "created_by_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
            index=True,
        ),
        sa.Column("import_code", sa.String(length=128), nullable=False, index=True),
        sa.Column("status", sa.String(length=64), nullable=False, server_default=sa.text("'created'")),
        sa.Column(
            "request_payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("result_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_kaspi_goods_imports_company", "kaspi_goods_imports", ["company_id"])
    op.create_index("ix_kaspi_goods_imports_import_code", "kaspi_goods_imports", ["import_code"])
    op.create_index("ix_kaspi_goods_imports_created_by", "kaspi_goods_imports", ["created_by_user_id"])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_kaspi_goods_imports_created_by", table_name="kaspi_goods_imports")
    op.drop_index("ix_kaspi_goods_imports_import_code", table_name="kaspi_goods_imports")
    op.drop_index("ix_kaspi_goods_imports_company", table_name="kaspi_goods_imports")
    op.drop_table("kaspi_goods_imports")
