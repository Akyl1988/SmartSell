# alembic/versions/20251003_update_payments_customer_fk.py
import sqlalchemy as sa

from alembic import op

revision = "20251003_update_payments_customer_fk"
down_revision = "20251003_create_inventory_outbox"


def upgrade():
    with op.batch_alter_table("payments") as b:
        b.alter_column("customer_id", existing_type=sa.Integer(), nullable=True)
        # гарантировать FK
        b.create_foreign_key(
            "fk_payments_customer_id", "customers", ["customer_id"], ["id"], ondelete="SET NULL"
        )


def downgrade():
    with op.batch_alter_table("payments") as b:
        b.drop_constraint("fk_payments_customer_id", type_="foreignkey")
