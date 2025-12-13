# migrations/versions/5231acbe568d_kaspi_tokens_and_price.py

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

# revision identifiers, used by Alembic.
revision = "5231acbe568d"
down_revision = "48e583d830c1"
branch_labels = None
depends_on = None

PUBLIC = "public"

def _table_exists(conn, fqname: str) -> bool:
    # fqname like 'public.kaspi_store_tokens'
    res = conn.exec_driver_sql("SELECT to_regclass(%s)", (fqname,)).scalar()
    return res is not None

def upgrade():
    conn = op.get_bind()

    # kaspi_store_tokens
    if not _table_exists(conn, "public.kaspi_store_tokens"):
        op.create_table(
            "kaspi_store_tokens",
            sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
            sa.Column("company_external_id", sa.String(64)),
            sa.Column("store_id", sa.String(64)),
            sa.Column("provider", sa.String(32), nullable=False, server_default=sa.text("'kaspi'")),
            sa.Column("key_id", sa.String(128)),
            sa.Column("api_key_encrypted", sa.Text, nullable=False),
            sa.Column("meta", sa.Text),
            sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.text("true")),
            sa.Column("created_at", sa.TIMESTAMP(timezone=False), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.Column("updated_at", sa.TIMESTAMP(timezone=False), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.UniqueConstraint("store_id", name="uq_kaspi_store_tokens_store_id"),
            schema=PUBLIC,
        )
        op.create_index("ix_kaspi_store_tokens_active", "kaspi_store_tokens", ["is_active"], unique=False, schema=PUBLIC)
        op.create_index("ix_kaspi_store_tokens_company", "kaspi_store_tokens", ["company_external_id"], unique=False, schema=PUBLIC)

    # product_marketplace_price
    if not _table_exists(conn, "public.product_marketplace_price"):
        op.create_table(
            "product_marketplace_price",
            sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
            sa.Column("product_id", sa.BigInteger, nullable=False),
            sa.Column("marketplace", sa.String(32), nullable=False),
            sa.Column("price", sa.Numeric(14, 2), nullable=False),
            sa.Column("currency", sa.String(3), nullable=False, server_default=sa.text("'KZT'")),
            sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.text("true")),
            sa.Column("created_at", sa.TIMESTAMP(timezone=False), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.Column("updated_at", sa.TIMESTAMP(timezone=False), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.UniqueConstraint("product_id", "marketplace", name="uq_price_product_marketplace"),
            schema=PUBLIC,
        )
        op.create_index("ix_price_active", "product_marketplace_price", ["is_active"], unique=False, schema=PUBLIC)
        op.create_index("ix_price_marketplace", "product_marketplace_price", ["marketplace"], unique=False, schema=PUBLIC)
        op.create_index("ix_price_product", "product_marketplace_price", ["product_id"], unique=False, schema=PUBLIC)

def downgrade():
    # Не удаляем, если ALLOW_DROPS не включён — но Alembic сам вызовет даунгрейд, если попросите.
    op.drop_index("ix_price_product", table_name="product_marketplace_price", schema=PUBLIC)
    op.drop_index("ix_price_marketplace", table_name="product_marketplace_price", schema=PUBLIC)
    op.drop_index("ix_price_active", table_name="product_marketplace_price", schema=PUBLIC)
    op.drop_table("product_marketplace_price", schema=PUBLIC)

    op.drop_index("ix_kaspi_store_tokens_company", table_name="kaspi_store_tokens", schema=PUBLIC)
    op.drop_index("ix_kaspi_store_tokens_active", table_name="kaspi_store_tokens", schema=PUBLIC)
    op.drop_table("kaspi_store_tokens", schema=PUBLIC)
