# alembic/versions/20251027_001_kaspi_tokens_pgcrypto.py
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# ИД ревизии — можешь оставить как есть или использовать сгенерированный
revision = "20251027_001_kaspi_tokens_pgcrypto"
# ВАЖНО: замени на реальную предыдущую миграцию
down_revision = "<PUT_PREV_REVISION_HERE>"
branch_labels = None
depends_on = None

def upgrade():
    # Требуется расширение pgcrypto
    op.execute('CREATE EXTENSION IF NOT EXISTS "pgcrypto";')
    # Если нет gen_random_uuid(), можно включить uuid-ossp:
    # op.execute('CREATE EXTENSION IF NOT EXISTS "uuid-ossp";')

    op.create_table(
        "kaspi_store_tokens",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),  # или uuid_generate_v4()
        sa.Column("store_name", sa.String(length=120), nullable=False, unique=True),
        sa.Column("token_ciphertext", postgresql.BYTEA, nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=False),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=False),
                  server_default=sa.text("now()"), nullable=False),
    )
    op.create_index(
        "ix_kaspi_store_tokens_store_name",
        "kaspi_store_tokens",
        ["store_name"],
        unique=True,
    )

    # Триггер для updated_at
    op.execute("""
    CREATE OR REPLACE FUNCTION set_updated_at()
    RETURNS TRIGGER AS $$
    BEGIN
      NEW.updated_at = now();
      RETURN NEW;
    END;
    $$ LANGUAGE plpgsql;
    """)

    op.execute("""
    CREATE TRIGGER kaspi_store_tokens_set_updated_at
    BEFORE UPDATE ON kaspi_store_tokens
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
    """)

def downgrade():
    op.execute("DROP TRIGGER IF EXISTS kaspi_store_tokens_set_updated_at ON kaspi_store_tokens;")
    op.execute("DROP FUNCTION IF EXISTS set_updated_at;")
    op.drop_index("ix_kaspi_store_tokens_store_name", table_name="kaspi_store_tokens")
    op.drop_table("kaspi_store_tokens")
