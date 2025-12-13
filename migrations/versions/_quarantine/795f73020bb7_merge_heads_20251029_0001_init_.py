"""migrations/versions/795f73020bb7_merge_heads_20251029_0001_init_.py
------------------------------------------------------------------------------
MERGE: 20251029_0001_init + 7f16439876ce  →  795f73020bb7 (единая голова)

Назначение
- Это merge-ревизия. Она не меняет схему БД и не должна содержать DDL.
- Её единственная роль — связать две независимые ветки истории Alembic
  в одну непрерывную линию, чтобы далее можно было безопасно добавлять
  новые ревизии (например, добавление FK для billing_payments).

Почему без DDL
- Любые ALTER/CREATE в merge-скрипте усложняют откаты и будущие merge’и.
- DDL вносим в специализированные ревизии (до/после merge), как уже
  сделано в «add FKs to billing_payments».

Диагностика
- В upgrade/downgrade оставлены только безопасные NOTICE-сообщения
  (через exec_driver_sql) — они не меняют состояние БД,
  но помогают понять, что скрипт исполнился.
------------------------------------------------------------------------------
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# -----------------------------------------------------------------------------
# Alembic identifiers
# -----------------------------------------------------------------------------
revision: str = "795f73020bb7"
down_revision: Union[str, Sequence[str], None] = ("20251029_0001_init", "7f16439876ce")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _pg_notice(msg: str) -> None:
    """Печатает NOTICE в PostgreSQL (без изменения данных/схемы)."""
    try:
        bind = op.get_bind()
        # NOTICE безопасен; если СУБД не Postgres — просто пропускаем.
        bind.exec_driver_sql(f"DO $$ BEGIN RAISE NOTICE '%s'; END $$;" % msg)
    except Exception:
        # Без падений: на непостгресовых диалектах или без прав — молчим.
        pass


def upgrade() -> None:
    """
    MERGE-UPGRADE (NO-OP).

    НИЧЕГО НЕ МЕНЯЕМ В СХЕМЕ.
    Эта ревизия только фиксирует объединение голов:
      - 20251029_0001_init
      - 7f16439876ce
    в единую историю под ID 795f73020bb7.
    """
    _pg_notice(
        "Alembic MERGE upgrade: merged heads (20251029_0001_init, 7f16439876ce) -> 795f73020bb7"
    )
    # NO-OP: не добавлять DDL/данные сюда.


def downgrade() -> None:
    """
    MERGE-DOWNGRADE (NO-OP).

    Откат merge не выполняет DDL. Возврат к разветвлённой истории
    допускается только в процессе ручной разработки, но не в проде.
    """
    _pg_notice(
        "Alembic MERGE downgrade: 795f73020bb7 would split back to (20251029_0001_init, 7f16439876ce)"
    )
    # NO-OP: не добавлять DDL/данные сюда.
