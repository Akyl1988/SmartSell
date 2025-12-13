# revision identifiers, used by Alembic.
revision = "20251029_0001_init"
down_revision = None
branch_labels = None
depends_on = None

from alembic import op
import sqlalchemy as sa


def upgrade():
    # ШАГ 1. Таблицы без «замыкающих» FK
    op.create_table(
        "companies",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("owner_id", sa.BigInteger, nullable=True),  # FK добавим ШАГОМ 2
        sa.Column("created_at", sa.TIMESTAMP(timezone=False), server_default=sa.text("now()"), nullable=False),
    )

    op.create_table(
        "users",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("email", sa.Text, nullable=False, unique=True),
        sa.Column("company_id", sa.BigInteger, nullable=True),  # FK добавим ШАГОМ 2
        sa.Column("created_at", sa.TIMESTAMP(timezone=False), server_default=sa.text("now()"), nullable=False),
    )

    op.create_table(
        "subscriptions",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("company_id", sa.BigInteger, nullable=False),  # FK добавим ШАГОМ 2
        sa.Column("plan", sa.Text, nullable=False),
        sa.Column("next_billing_date", sa.DATE, nullable=True),
    )

    op.create_table(
        "billing_payments",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("subscription_id", sa.BigInteger, nullable=False),  # FK добавим ШАГОМ 2
        sa.Column("amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=False), server_default=sa.text("now()"), nullable=False),
    )

    # ШАГ 2. FK после того, как все таблицы существуют
    op.create_foreign_key(
        "fk_users_company",
        source_table="users",
        referent_table="companies",
        local_cols=["company_id"],
        remote_cols=["id"],
        ondelete="SET NULL",
        deferrable=True,
        initially="DEFERRED",
    )

    op.create_foreign_key(
        "fk_companies_owner",
        source_table="companies",
        referent_table="users",
        local_cols=["owner_id"],
        remote_cols=["id"],
        ondelete="SET NULL",
        deferrable=True,
        initially="DEFERRED",
    )

    op.create_foreign_key(
        "fk_subscriptions_company",
        source_table="subscriptions",
        referent_table="companies",
        local_cols=["company_id"],
        remote_cols=["id"],
        ondelete="CASCADE",
        deferrable=True,
        initially="DEFERRED",
    )

    op.create_foreign_key(
        "fk_billing_payments_subscription",
        source_table="billing_payments",
        referent_table="subscriptions",
        local_cols=["subscription_id"],
        remote_cols=["id"],
        ondelete="CASCADE",
        deferrable=True,
        initially="DEFERRED",
    )


def downgrade():
    op.drop_constraint("fk_billing_payments_subscription", "billing_payments", type_="foreignkey")
    op.drop_constraint("fk_subscriptions_company", "subscriptions", type_="foreignkey")
    op.drop_constraint("fk_companies_owner", "companies", type_="foreignkey")
    op.drop_constraint("fk_users_company", "users", type_="foreignkey")

    op.drop_table("billing_payments")
    op.drop_table("subscriptions")
    op.drop_table("users")
    op.drop_table("companies")
