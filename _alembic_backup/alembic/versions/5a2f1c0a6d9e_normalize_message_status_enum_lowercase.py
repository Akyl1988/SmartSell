"""Normalize messages.status ENUM to lowercase values

Revision ID: 5a2f1c0a6d9e
Revises: 37880826cca6
Create Date: 2025-10-13 06:25:16
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "5a2f1c0a6d9e"
down_revision = "37880826cca6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    dialect_name = bind.dialect.name

    if dialect_name != "postgresql":
        # Для непостгрес окружений — просто выходим (ENUM только в PG)
        return

    # 1) Убедимся, что колонка существует
    #    (в проде колонка messages.status уже есть)
    # 2) Создаём новый тип с нужными значениями (lowercase)
    op.execute("CREATE TYPE message_status_new AS ENUM ('pending','sent','delivered','failed')")

    # 3) Снимаем DEFAULT, приводим значения к lower(text)
    op.execute("ALTER TABLE messages ALTER COLUMN status DROP DEFAULT")
    op.execute("UPDATE messages SET status = lower(status::text) WHERE status IS NOT NULL")

    # 4) Меняем тип колонки на новый ENUM через USING
    op.execute(
        "ALTER TABLE messages "
        "ALTER COLUMN status TYPE message_status_new USING (lower(status::text))::message_status_new"
    )

    # 5) Удаляем старый тип и переименовываем новый в старое имя
    op.execute("DROP TYPE IF EXISTS message_status")
    op.execute("ALTER TYPE message_status_new RENAME TO message_status")

    # 6) Возвращаем DEFAULT (если в моделях он ожидается как 'pending')
    op.execute("ALTER TABLE messages ALTER COLUMN status SET DEFAULT 'pending'")


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    # Возврат к UPPERCASE значениями (если потребуется откат)
    op.execute("CREATE TYPE message_status_old AS ENUM ('PENDING','SENT','DELIVERED','FAILED')")
    op.execute("ALTER TABLE messages ALTER COLUMN status DROP DEFAULT")
    op.execute("UPDATE messages SET status = upper(status::text) WHERE status IS NOT NULL")
    op.execute(
        "ALTER TABLE messages "
        "ALTER COLUMN status TYPE message_status_old USING (upper(status::text))::message_status_old"
    )
    op.execute("DROP TYPE IF EXISTS message_status")
    op.execute("ALTER TYPE message_status_old RENAME TO message_status")
    op.execute("ALTER TABLE messages ALTER COLUMN status SET DEFAULT 'PENDING'")
