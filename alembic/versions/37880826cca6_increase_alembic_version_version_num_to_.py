"""Increase alembic_version.version_num to VARCHAR(128)

Revision ID: 37880826cca6
Revises: b0975f9de58d
Create Date: 2025-09-20 12:21:48.972511

"""
from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from sqlalchemy.exc import SQLAlchemyError

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "37880826cca6"
down_revision: Union[str, Sequence[str], None] = "b0975f9de58d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Увеличить длину столбца version_num до VARCHAR(128), если таблица существует
    try:
        conn = op.get_bind()
        # Проверяем, существует ли таблица
        table_exists = conn.execute(
            sa.text(
                """
                SELECT count(*) FROM information_schema.tables
                WHERE table_schema='public' AND table_name='alembic_version'
            """
            )
        ).scalar()
        if table_exists:
            # Проверяем текущий тип столбца
            length = conn.execute(
                sa.text(
                    """
                    SELECT character_maximum_length
                    FROM information_schema.columns
                    WHERE table_name='alembic_version' AND column_name='version_num'
                """
                )
            ).scalar()
            if length is not None and length < 128:
                op.execute(
                    "ALTER TABLE public.alembic_version ALTER COLUMN version_num TYPE VARCHAR(128);"
                )
                print("version_num успешно расширен до VARCHAR(128)")
            else:
                print("version_num уже VARCHAR(128) или более, ничего менять не нужно.")
        else:
            print(
                "Таблица alembic_version не найдена. Она должна быть создана Alembic-ом автоматически."
            )
    except SQLAlchemyError as e:
        print(f"Ошибка при изменении типа version_num: {e}")


def downgrade() -> None:
    """Downgrade schema."""
    try:
        conn = op.get_bind()
        table_exists = conn.execute(
            sa.text(
                """
                SELECT count(*) FROM information_schema.tables
                WHERE table_schema='public' AND table_name='alembic_version'
            """
            )
        ).scalar()
        if table_exists:
            length = conn.execute(
                sa.text(
                    """
                    SELECT character_maximum_length
                    FROM information_schema.columns
                    WHERE table_name='alembic_version' AND column_name='version_num'
                """
                )
            ).scalar()
            if length is not None and length > 32:
                op.execute(
                    "ALTER TABLE public.alembic_version ALTER COLUMN version_num TYPE VARCHAR(32);"
                )
                print("version_num уменьшен до VARCHAR(32)")
            else:
                print("version_num уже VARCHAR(32) или менее, ничего менять не нужно.")
        else:
            print("Таблица alembic_version не найдена.")
    except SQLAlchemyError as e:
        print(f"Ошибка при откате типа version_num: {e}")
