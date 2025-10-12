# alembic/versions/20251003_create_customers.py
import sqlalchemy as sa

from alembic import op

revision = "20251003_create_customers"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "customers",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("full_name", sa.String(255)),
        sa.Column("phone", sa.String(32)),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime, nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_customers_email", "customers", ["email"], unique=True)


def downgrade():
    op.drop_table("customers")
