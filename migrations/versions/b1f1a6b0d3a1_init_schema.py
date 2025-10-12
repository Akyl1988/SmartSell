"""
Initial baseline for Alembic (SmartSell).

Что делает этот baseline и почему он безопасен:
- Фиксирует стартовую ревизию, чтобы Alembic начал отслеживать схему (таблица alembic_version).
- Тихо и идемпотентно включает полезные расширения PostgreSQL (если есть права).
- Безопасно переименовывает «наследованные» ENUM-типы (messagestatus → message_status, campaignstatus → campaign_status).
- Гарантирует наличие схемы (по умолчанию public) и выставляет search_path на неё на время миграции.
- Проставляет временную зону сессии на «Asia/Almaty» (Астана, UTC+5) — без изменения данных.
- Может заранее «завести» перечисления ENUM, чтобы будущие ревизии не падали на их создании.
  (ВАЖНО: Alembic/SQLAlchemy при создании таблиц вызывают enum.create(checkfirst=False);
   если тип уже существует, возможен конфликт. Этот baseline создаёт нужные типы заранее.)
- НЕ выполняет разрушительных действий: upgrade()/downgrade() — no-op для таблиц/индексов/данных.

Если далее нужны реальные изменения:
1) Применить baseline:    alembic upgrade head
2) Сгенерировать дельту:  alembic revision --autogenerate -m "sync models"
3) Применить дельту:      alembic upgrade head

Совет по моделям/ревизиям с ENUM:
- В колонках со строковыми ENUM указывайте name="<enum_name>" и create_type=False
  (sa.Enum(..., name="orderstatus", create_type=False)), если тип уже создаётся отдельно/заранее.
- Либо используйте этот baseline для предварительного создания типов и не давайте ревизии их пересоздавать.
"""

from __future__ import annotations

import os
from collections.abc import Iterable
from logging import getLogger
from typing import Optional

from alembic import op

# ──────────────────────────────────────────────────────────────────────────────
# Alembic identifiers
# ──────────────────────────────────────────────────────────────────────────────
# ВАЖНО: оставьте стабильный revision-id — он «застампится» в alembic_version.
revision = "b1f1a6b0d3a1"
down_revision = None
branch_labels = None
depends_on = None


# ──────────────────────────────────────────────────────────────────────────────
# Constants & logger
# ──────────────────────────────────────────────────────────────────────────────
DEFAULT_SCHEMA = "public"
# Базовая TZ проекта (по ТЗ «Астанинская +5 по UTC»)
DEFAULT_TZ = os.getenv("APP_TIMEZONE", "Asia/Almaty")
logger = getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────────────
# Low-level helpers (defensive, idempotent)
# ──────────────────────────────────────────────────────────────────────────────
def _bind():
    """Активное соединение SQLAlchemy для сырых SQL."""
    return op.get_bind()


def _is_postgres() -> bool:
    """Проверяем, что backend — PostgreSQL."""
    try:
        return _bind().dialect.name == "postgresql"
    except Exception:
        return False


def _safe_exec(sql: str) -> None:
    """
    Выполнить SQL, проглотить ошибку (нет прав/объект уже есть/и т.п.).
    Baseline должен быть максимально «мягким» и идемпотентным.
    """
    try:
        _bind().exec_driver_sql(sql)
    except Exception as ex:
        # Не шумим, но оставим тонкий след в debug-логах (если включены).
        logger.debug(
            "SAFE-EXEC skipped/failed: %s; error=%r",
            sql.strip().splitlines()[0],
            ex,
        )


def _ensure_schema(schema: str = DEFAULT_SCHEMA) -> None:
    """Создаёт схему, если её нет (без фатала)."""
    if not _is_postgres():
        return
    _safe_exec(f'CREATE SCHEMA IF NOT EXISTS "{schema}";')


def _set_search_path(schema: str = DEFAULT_SCHEMA) -> None:
    """Устанавливает search_path на целевую схему в рамках миграции (сеансово)."""
    if not _is_postgres():
        return
    _safe_exec(f'SET LOCAL search_path = "{schema}";')


def _set_session_timezone(tz: str = DEFAULT_TZ) -> None:
    """
    Ставит TZ сессии для этой миграции (без влияния на данные/серверный timezone).
    Никаких конвертаций значений в таблицах не производится.
    """
    if not _is_postgres():
        return
    # В PostgreSQL корректно: SET LOCAL TIME ZONE 'Asia/Almaty';
    _safe_exec(f"SET LOCAL TIME ZONE '{tz}';")


def _set_pg_timeouts(
    lock_timeout_ms: int = 15000,
    statement_timeout_ms: int = 0,
    idle_in_txn_session_timeout_ms: int = 0,
) -> None:
    """
    Нестрого обязательно, но помогает не «зависнуть» на блокировках на длительное время.
    0 — означает без ограничения.
    """
    if not _is_postgres():
        return
    _safe_exec(f"SET LOCAL lock_timeout = {lock_timeout_ms};")
    _safe_exec(f"SET LOCAL statement_timeout = {statement_timeout_ms};")
    _safe_exec(f"SET LOCAL idle_in_transaction_session_timeout = {idle_in_txn_session_timeout_ms};")


def _enable_pg_extensions() -> None:
    """Полезные расширения PostgreSQL (идемпотентно)."""
    if not _is_postgres():
        return
    _safe_exec('CREATE EXTENSION IF NOT EXISTS "uuid-ossp";')
    _safe_exec('CREATE EXTENSION IF NOT EXISTS "pgcrypto";')
    # Часто полезны для индексов/оптимизаций
    _safe_exec('CREATE EXTENSION IF NOT EXISTS "btree_gin";')
    _safe_exec('CREATE EXTENSION IF NOT EXISTS "btree_gist";')


def _rename_legacy_enums() -> None:
    """
    Если в базе есть старые ENUM-имена, переименуем их в современные,
    чтобы SQLAlchemy/Alembic не пытались создавать дубликаты.
    """
    if not _is_postgres():
        return
    _safe_exec(
        """
        DO $$
        BEGIN
            -- message status
            IF EXISTS (
                SELECT 1 FROM pg_type t
                JOIN pg_namespace n ON n.oid = t.typnamespace
                WHERE t.typname = 'messagestatus' AND n.nspname = 'public'
            ) THEN
                ALTER TYPE public.messagestatus RENAME TO message_status;
            END IF;

            -- campaign status
            IF EXISTS (
                SELECT 1 FROM pg_type t
                JOIN pg_namespace n ON n.oid = t.typnamespace
                WHERE t.typname = 'campaignstatus' AND n.nspname = 'public'
            ) THEN
                ALTER TYPE public.campaignstatus RENAME TO campaign_status;
            END IF;

            -- Примечание: часто встречается слитное имя статуса заказа 'orderstatus'.
            -- Переименовывать его автоматически НЕ будем — многие проекты используют именно это имя.
        END
        $$;
        """
    )


def _ensure_enum(name: str, values: Iterable[str], schema: str = DEFAULT_SCHEMA) -> None:
    """
    Создаёт ENUM-тип, если его нет (через DO $$ ...).
    Важно: Alembic при create_table вызывает enum.create(checkfirst=False),
    поэтому наличие типа заранее предотвращает попытку "CREATE TYPE ..." в момент создания таблицы.
    """
    if not _is_postgres():
        return
    # Собираем строку ENUM-значений с экранированием
    safe_vals = ", ".join("'" + str(v).replace("'", "''") + "'" for v in values)
    _safe_exec(
        f"""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1
                FROM pg_type t
                JOIN pg_namespace n ON n.oid = t.typnamespace
                WHERE t.typname = '{name}' AND n.nspname = '{schema}'
            ) THEN
                EXECUTE 'CREATE TYPE "{schema}"."{name}" AS ENUM ({safe_vals})';
            END IF;
        END
        $$;
        """
    )


def _ensure_enum_values(name: str, new_values: Iterable[str], schema: str = DEFAULT_SCHEMA) -> None:
    """
    Безопасно добавляет недостающие значения в ENUM-тип (PostgreSQL ≥ 12 поддерживает IF NOT EXISTS).
    Порядок не гарантируется (для большинства бизнес-кейсов это нормально).
    """
    if not _is_postgres():
        return
    for v in new_values:
        sv = str(v).replace("'", "''")
        _safe_exec(f'ALTER TYPE "{schema}"."{name}" ADD VALUE IF NOT EXISTS \'{sv}\';')


def _enum_exists(name: str, schema: str = DEFAULT_SCHEMA) -> bool:
    """Проверка существования enum-типа."""
    if not _is_postgres():
        return False
    res = (
        _bind()
        .exec_driver_sql(
            """
        SELECT 1
        FROM pg_type t
        JOIN pg_namespace n ON n.oid = t.typnamespace
        WHERE t.typname = %(name)s AND n.nspname = %(schema)s
        """,
            {"name": name, "schema": schema},
        )
        .fetchone()
    )
    return bool(res)


def _ensure_baseline_enums() -> None:
    """
    Предварительно создаём часто используемые ENUM-типы проекта,
    чтобы будущие ревизии с create_table не падали из-за DuplicateObject.
    Отредактируйте список под проект при необходимости.
    """
    # Современные имена
    _ensure_enum("message_status", ["PENDING", "SENT", "DELIVERED", "FAILED"])
    _ensure_enum("campaign_status", ["DRAFT", "ACTIVE", "PAUSED", "COMPLETED"])

    # Исторически используемые имена (встречаются в текущих моделях/ревизиях)
    _ensure_enum(
        "orderstatus",
        [
            "PENDING",
            "CONFIRMED",
            "PAID",
            "PROCESSING",
            "SHIPPED",
            "DELIVERED",
            "COMPLETED",
            "CANCELLED",
            "REFUNDED",
        ],
    )
    _ensure_enum("paymentprovider", ["TIPTOP", "KASPI", "PAYBOX", "MANUAL"])
    _ensure_enum("reconciliationstatus", ["MATCHED", "MISSING", "MISMATCH"])
    _ensure_enum("message_channel", ["EMAIL", "WHATSAPP", "TELEGRAM", "SMS", "PUSH", "VIBER"])
    _ensure_enum("paymentmethod", ["CARD", "CASH", "BANK_TRANSFER", "WALLET", "QR_CODE"])
    _ensure_enum(
        "paymentstatus",
        [
            "PENDING",
            "PROCESSING",
            "SUCCESS",
            "FAILED",
            "CANCELLED",
            "REFUNDED",
            "PARTIALLY_REFUNDED",
        ],
    )

    # Пример расширения значений (оставлено как шаблон):
    # if _enum_exists("paymentstatus"):
    #     _ensure_enum_values("paymentstatus", ["CHARGEBACK"])


def _ensure_version_table_schema(schema: Optional[str] = DEFAULT_SCHEMA) -> None:
    """
    Если alembic.version_table_schema в env.py/alembic.ini настроена на public,
    убедимся что схема существует (на случай, если кто-то её удалил).
    """
    if not schema:
        return
    _ensure_schema(schema)


def _log_environment_summary() -> None:
    """Мини-диагностика, чтобы в логах было видно окружение."""
    try:
        dialect = _bind().dialect
        logger.info(
            "ALEMBIC BASELINE: dialect=%s, server_version=%s, tz=%s",
            getattr(dialect, "name", "unknown"),
            getattr(dialect, "server_version_info", None),
            DEFAULT_TZ,
        )
    except Exception:
        pass


# ──────────────────────────────────────────────────────────────────────────────
# Upgrade / Downgrade
# ──────────────────────────────────────────────────────────────────────────────
def upgrade() -> None:
    """Baseline: мягкие подготовительные шаги, без схемных изменений таблиц."""
    _log_environment_summary()

    # Настройки сеанса (необязательно, но полезно и безвредно).
    _set_pg_timeouts(
        lock_timeout_ms=15000, statement_timeout_ms=0, idle_in_txn_session_timeout_ms=0
    )
    _set_session_timezone(DEFAULT_TZ)

    # 1) Убедимся, что схема существует и работаем в ней.
    _ensure_schema(DEFAULT_SCHEMA)
    _ensure_version_table_schema(DEFAULT_SCHEMA)
    _set_search_path(DEFAULT_SCHEMA)

    # 2) Включим полезные расширения PostgreSQL (если есть права).
    _enable_pg_extensions()

    # 3) Переименуем устаревшие ENUM-имена, если встречаются.
    _rename_legacy_enums()

    # 4) Заранее создадим используемые проектом ENUM-типы, чтобы
    #    последующие ревизии (например, "sync models") не упали на CREATE TYPE.
    _ensure_baseline_enums()

    # НИКАКИХ CREATE/ALTER/DROP для таблиц в baseline — только подготовка.


def downgrade() -> None:
    """
    Откат baseline: осознанно «no-op», чтобы не разрушать прод.
    Можно добавить обратные переименования ENUM, но обычно baseline не откатывают.
    """
    # Пример обратного переименования (оставлено как справочный шаблон):
    # _safe_exec(
    #     '''
    #     DO $$
    #     BEGIN
    #         IF EXISTS (
    #             SELECT 1 FROM pg_type t JOIN pg_namespace n ON n.oid = t.typnamespace
    #             WHERE t.typname = 'message_status' AND n.nspname = 'public'
    #         ) THEN
    #             ALTER TYPE public.message_status RENAME TO messagestatus;
    #         END IF;
    #         IF EXISTS (
    #             SELECT 1 FROM pg_type t JOIN pg_namespace n ON n.oid = t.typnamespace
    #             WHERE t.typname = 'campaign_status' AND n.nspname = 'public'
    #         ) THEN
    #             ALTER TYPE public.campaign_status RENAME TO campaignstatus;
    #         END IF;
    #     END
    #     $$;
    #     '''
    # )
    pass
