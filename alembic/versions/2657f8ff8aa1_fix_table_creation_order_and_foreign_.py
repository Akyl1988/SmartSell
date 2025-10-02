"""Fix table creation order and foreign keys for products & product_variants

Revision ID: 2657f8ff8aa1
Revises: 20240914_add_campaign_and_audit_models
Create Date: 2024-09-15 08:00:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect, text

# --- Alembic identifiers ---
revision = "2657f8ff8aa1"
down_revision = "20240914_add_campaign_and_audit_models"
branch_labels = None
depends_on = None


# ---------------------------
# Helpers (idempotent & safe)
# ---------------------------


def _inspector():
    bind = op.get_bind()
    return inspect(bind), bind


def table_exists(table_name: str, schema: str | None = None) -> bool:
    insp, _ = _inspector()
    return table_name in insp.get_table_names(schema=schema)


def column_exists(table_name: str, column_name: str, schema: str | None = None) -> bool:
    insp, _ = _inspector()
    if not table_exists(table_name, schema):
        return False
    return any(col["name"] == column_name for col in insp.get_columns(table_name, schema=schema))


def index_exists(table_name: str, index_name: str, schema: str | None = None) -> bool:
    insp, _ = _inspector()
    if not table_exists(table_name, schema):
        return False
    return any(ix["name"] == index_name for ix in insp.get_indexes(table_name, schema=schema))


def fk_exists(table_name: str, fk_name: str, schema: str | None = None) -> bool:
    insp, _ = _inspector()
    if not table_exists(table_name, schema):
        return False
    return any(fk.get("name") == fk_name for fk in insp.get_foreign_keys(table_name, schema=schema))


def create_index_safe(
    index_name: str, table_name: str, columns: list[str], unique: bool = False
) -> None:
    if table_exists(table_name) and not index_exists(table_name, index_name):
        op.create_index(index_name, table_name, columns, unique=unique)


def add_column_safe(table_name: str, column: sa.Column, schema: str | None = None) -> None:
    if table_exists(table_name, schema) and not column_exists(table_name, column.name, schema):
        op.add_column(table_name, column)


def create_fk_safe(
    fk_name: str,
    source_table: str,
    referent_table: str,
    local_cols: list[str],
    remote_cols: list[str],
    ondelete: str | None = None,
    source_schema: str | None = None,
    referent_schema: str | None = None,
) -> None:
    if not table_exists(source_table, source_schema) or not table_exists(
        referent_table, referent_schema
    ):
        return
    if fk_exists(source_table, fk_name, source_schema):
        return
    op.create_foreign_key(
        fk_name,
        source_table,
        referent_table,
        local_cols,
        remote_cols,
        ondelete=ondelete,
        source_schema=source_schema,
        referent_schema=referent_schema,
    )


# ---------------------------
# Upgrade / Downgrade
# ---------------------------


def upgrade():
    """
    Гарантируем:
    - есть таблица products (минимально необходимая схема);
    - есть таблица product_variants (минимально необходимая схема);
    - есть FK product_variants.product_id → products.id (ON DELETE CASCADE);
    - есть полезные индексы.
    Всё — с проверками существования, без падений на «relation does not exist».
    """

    # 1) PRODUCTS
    if not table_exists("products"):
        op.create_table(
            "products",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("name", sa.String(255), nullable=False, index=True),
            sa.Column("sku", sa.String(64), nullable=True, unique=True, index=True),
            sa.Column("price", sa.Numeric(12, 2), nullable=True),
            sa.Column(
                "is_active",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("TRUE"),
                index=True,
            ),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        )
    else:
        # Если products уже есть, добавим недостающие ключевые поля (мягко, без потери данных)
        add_column_safe("products", sa.Column("id", sa.Integer(), primary_key=True))
        add_column_safe("products", sa.Column("name", sa.String(255), nullable=False))
        add_column_safe("products", sa.Column("sku", sa.String(64)))
        add_column_safe("products", sa.Column("price", sa.Numeric(12, 2)))
        add_column_safe(
            "products",
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("TRUE")),
        )
        add_column_safe(
            "products",
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        )
        add_column_safe(
            "products",
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        )

    create_index_safe("ix_products_name", "products", ["name"])
    create_index_safe("ix_products_is_active", "products", ["is_active"])

    # 2) PRODUCT_VARIANTS
    if not table_exists("product_variants"):
        op.create_table(
            "product_variants",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("product_id", sa.Integer(), nullable=False),
            sa.Column("variant_name", sa.String(255), nullable=True, index=True),
            sa.Column("sku", sa.String(64), nullable=True, unique=True, index=True),
            sa.Column("price", sa.Numeric(12, 2), nullable=True),
            sa.Column("stock", sa.Integer(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        )
    else:
        add_column_safe("product_variants", sa.Column("id", sa.Integer(), primary_key=True))
        add_column_safe("product_variants", sa.Column("product_id", sa.Integer(), nullable=False))
        add_column_safe("product_variants", sa.Column("variant_name", sa.String(255)))
        add_column_safe("product_variants", sa.Column("sku", sa.String(64)))
        add_column_safe("product_variants", sa.Column("price", sa.Numeric(12, 2)))
        add_column_safe("product_variants", sa.Column("stock", sa.Integer()))
        add_column_safe(
            "product_variants",
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        )
        add_column_safe(
            "product_variants",
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        )

    create_index_safe("ix_product_variants_variant_name", "product_variants", ["variant_name"])

    # 3) FK: product_variants.product_id → products.id (ON DELETE CASCADE)
    create_fk_safe(
        "fk_product_variants_product_id",
        source_table="product_variants",
        referent_table="products",
        local_cols=["product_id"],
        remote_cols=["id"],
        ondelete="CASCADE",
    )


def downgrade():
    """
    Откатываем безопасно:
    - снимаем FK, если есть;
    - удаляем индексы;
    - удаляем таблицы (variants затем products).
    """

    # Снимем FK, если он есть
    if fk_exists("product_variants", "fk_product_variants_product_id"):
        op.drop_constraint("fk_product_variants_product_id", "product_variants", type_="foreignkey")

    # Дроп индексов (если существуют)
    for name, table in [
        ("ix_product_variants_variant_name", "product_variants"),
        ("ix_products_is_active", "products"),
        ("ix_products_name", "products"),
    ]:
        if index_exists(table, name):
            op.drop_index(name, table_name=table)

    # Удаляем таблицы в правильном порядке
    if table_exists("product_variants"):
        op.drop_table("product_variants")
    if table_exists("products"):
        op.drop_table("products")
