from __future__ import annotations

import asyncio
import logging
import os
import sys
from logging.config import fileConfig
from pathlib import Path
from typing import Any, Optional

from sqlalchemy import event, pool
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.engine.create import create_engine
from sqlalchemy.engine.url import make_url
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from alembic import context

# =============================================================================
# 🧭 Поиск корня проекта и sys.path
# =============================================================================
THIS_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = THIS_DIR.parent  # .../alembic -> корень проекта
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# =============================================================================
# 🧩 Alembic config и логирование
# =============================================================================
config = context.config

# Подхватываем ini-логирование Alembic (если определено)
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

logger = logging.getLogger("alembic.env")

# =============================================================================
# 🕒 Базовая временная зона проекта (по ТЗ)
# =============================================================================
# В Казахстане сейчас UTC+5, используем "Asia/Almaty".
DEFAULT_TZ = os.getenv("APP_TIMEZONE", "Asia/Almaty")


# =============================================================================
# 📥 Загрузка .env (тихо, если нет python-dotenv)
# =============================================================================
def load_dotenv_silently() -> None:
    """
    Загружает переменные окружения из .env/.env.local, если установлен python-dotenv.
    Если пакета нет — тихо пропускаем.
    """
    try:
        from dotenv import load_dotenv  # type: ignore
    except Exception:
        return

    # приоритет: .env.local > .env
    for candidate in (PROJECT_ROOT / ".env.local", PROJECT_ROOT / ".env"):
        if candidate.exists():
            load_dotenv(dotenv_path=candidate, override=False)
            logger.info("Loaded environment from %s", candidate)


load_dotenv_silently()


# =============================================================================
# ⚙️ Определение URLs БД
# =============================================================================
def is_testing_env() -> bool:
    """Определяем, запускаются ли миграции в тестовой среде."""
    if os.getenv("PYTEST_CURRENT_TEST"):
        return True
    env = (os.getenv("ENVIRONMENT") or os.getenv("PYTHON_ENV") or "").lower()
    return env in {"test", "testing"}


def _get_db_url_from_settings() -> Optional[str]:
    """
    Пробуем достать URL из pydantic-настроек приложения:
    app.core.config.get_settings().DATABASE_URL / TEST_DATABASE_URL.
    Если импорт неудачен — возвращаем None.
    """
    try:
        from app.core.config import get_settings  # type: ignore

        s = get_settings()
        if not s:
            return None

        if is_testing_env() and getattr(s, "TEST_DATABASE_URL", None):
            return str(s.TEST_DATABASE_URL)
        if getattr(s, "DATABASE_URL", None):
            return str(s.DATABASE_URL)
    except Exception as e:
        logger.debug("Skip settings import: %s", e)
    return None


def get_database_url() -> str:
    """
    Приоритет:
      1) ALEMBIC_DATABASE_URL
      2) TEST_DATABASE_URL (если тесты)
      3) DATABASE_URL
      4) app.core.config.Settings
      5) sqlalchemy.url из alembic.ini
    """
    url = (os.getenv("ALEMBIC_DATABASE_URL") or "").strip()
    if url:
        return url

    if is_testing_env():
        url = (os.getenv("TEST_DATABASE_URL") or "").strip()
        if url:
            return url

    url = (os.getenv("DATABASE_URL") or "").strip()
    if url:
        return url

    url = _get_db_url_from_settings()
    if url:
        return url.strip()

    url = (config.get_main_option("sqlalchemy.url") or "").strip()
    if url:
        return url

    raise RuntimeError(
        "DATABASE_URL is not set. "
        "Set env var DATABASE_URL (or TEST_DATABASE_URL under pytest), e.g. "
        "postgresql+psycopg2://postgres:admin123@localhost:5432/smartsell2"
    )


DATABASE_URL = get_database_url()
config.set_main_option("sqlalchemy.url", DATABASE_URL)

# =============================================================================
# 🗂️ Метаданные моделей приложения
# =============================================================================
try:
    from app.database.base import Base  # noqa: E402
except Exception as e:
    raise RuntimeError("Failed to import Base metadata from app.database.base") from e

target_metadata = Base.metadata


def try_import_models() -> None:
    """
    Импортируем пакеты с моделями, чтобы autogenerate видел все таблицы.
    Если в app/models/__init__.py всё уже подтягивается — одного импорта достаточно.
    """
    try:
        import app.models  # noqa: F401

        logger.info("Imported app.models for autogenerate")
    except Exception as e:
        logger.warning("Could not import app.models: %s", e)


try_import_models()

# =============================================================================
# 🏷️ Naming convention (если не задано в Base.metadata)
# =============================================================================
if not getattr(target_metadata, "naming_convention", None):
    target_metadata.naming_convention = {
        "ix": "ix_%(column_0_label)s",
        "uq": "uq_%(table_name)s_%(column_0_name)s",
        "ck": "ck_%(table_name)s_%(constraint_name)s",
        "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
        "pk": "pk_%(table_name)s",
    }


# =============================================================================
# 🔍 Фильтры/хуки Alembic
# =============================================================================
def include_object(object: Any, name: str, type_: str, reflected: bool, compare_to: Any) -> bool:
    """
    Позволяет исключить служебные/внешние объекты из автогенерации.
    По умолчанию — пропускаем всё.
    """
    # Пример исключения системных таблиц:
    # if type_ == "table" and name.startswith("pg_"):
    #     return False
    return True


def process_revision_directives(context_: Any, revision: Any, directives: list[Any]) -> None:
    """
    Удаляем «пустые» ревизии при autogenerate, чтобы не плодить пустые файлы.
    """
    if getattr(config.cmd_opts, "autogenerate", False):
        script = directives[0]
        if script.upgrade_ops.is_empty():
            directives[:] = []
            logger.info("No schema changes detected; skipping empty revision.")


# =============================================================================
# 🧪 Общие опции сравнения/рендеринга
# =============================================================================
def _detect_sqlite(url: str) -> bool:
    try:
        return make_url(url).get_backend_name().startswith("sqlite")
    except Exception:
        return url.startswith("sqlite")


def _detect_async(url: str) -> bool:
    try:
        backend = make_url(url).get_backend_name()
        return backend.endswith("+asyncpg") or backend.startswith("async")
    except Exception:
        return "+asyncpg" in url or url.startswith("postgresql+asyncpg")


def _bool_env(name: str, default: bool) -> bool:
    v = os.getenv(name)
    if v is None:
        return default
    return str(v).strip().lower() in {"1", "true", "yes", "y", "on"}


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except Exception:
        return default


def make_context_kwargs(connection: Connection | None = None) -> dict[str, Any]:
    """
    Общие настройки для context.configure(...).
    """
    url = (
        str(connection.engine.url)
        if connection is not None
        else config.get_main_option("sqlalchemy.url")
    )
    render_as_batch = _detect_sqlite(url)

    include_schemas = _bool_env("ALEMBIC_INCLUDE_SCHEMAS", False)
    version_table = os.getenv("ALEMBIC_VERSION_TABLE") or "alembic_version"
    version_table_schema = os.getenv("ALEMBIC_VERSION_TABLE_SCHEMA") or None

    return dict(
        connection=connection,
        target_metadata=target_metadata,
        include_object=include_object,
        process_revision_directives=process_revision_directives,
        compare_type=True,
        compare_server_default=True,
        render_as_batch=render_as_batch,
        include_schemas=include_schemas,
        version_table=version_table,
        version_table_schema=version_table_schema,
        timezone=DEFAULT_TZ,  # только для информации/логов; сами миграции TZ не конвертируют
    )


# =============================================================================
# 🧵 Offline миграции
# =============================================================================
def run_migrations_offline() -> None:
    """
    Запуск миграций в offline-режиме (генерация SQL без подключения к БД).
    """
    url = config.get_main_option("sqlalchemy.url")
    if not url:
        raise RuntimeError("sqlalchemy.url is not set for offline migrations")

    context.configure(
        url=url,
        target_metadata=target_metadata,
        include_object=include_object,
        process_revision_directives=process_revision_directives,
        literal_binds=True,
        compare_type=True,
        compare_server_default=True,
        render_as_batch=True,  # оффлайн безопаснее держать включенным
        include_schemas=_bool_env("ALEMBIC_INCLUDE_SCHEMAS", False),
        version_table=os.getenv("ALEMBIC_VERSION_TABLE") or "alembic_version",
        version_table_schema=os.getenv("ALEMBIC_VERSION_TABLE_SCHEMA") or None,
    )

    with context.begin_transaction():
        context.run_migrations()


# =============================================================================
# 🔌 Параметры подключения и обработчики
# =============================================================================
def _engine_options_from_env() -> dict[str, Any]:
    """
    Безопасно вычитываем опции пула/эха из окружения.
    Используются только для sync-движка.
    """
    return {
        "pool_size": _int_env("SQLALCHEMY_POOL_SIZE", 5),
        "max_overflow": _int_env("SQLALCHEMY_MAX_OVERFLOW", 10),
        "pool_pre_ping": _bool_env("SQLALCHEMY_POOL_PRE_PING", True),
        "pool_recycle": _int_env("SQLALCHEMY_POOL_RECYCLE", 1800),
        "echo": _bool_env("SQLALCHEMY_ECHO", False),
        # "poolclass": pool.QueuePool,  # по умолчанию
        "future": True,
    }


def _build_sync_engine(url: str) -> Engine:
    """
    Создает синхронный Engine. Если ALEMBIC_DISABLE_POOL=1 — используем NullPool.
    """
    opts = _engine_options_from_env()
    if _bool_env("ALEMBIC_DISABLE_POOL", False):
        opts["poolclass"] = pool.NullPool
    engine = create_engine(url, **opts)

    # Для SQLite включаем foreign_keys
    if _detect_sqlite(url):

        @event.listens_for(engine, "connect")
        def _set_sqlite_pragma(dbapi_conn, _):
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    return engine


def _build_async_engine(url: str) -> AsyncEngine:
    """
    Создает асинхронный AsyncEngine.
    Пул можно отключить переменной ALEMBIC_DISABLE_POOL=1.
    """
    opts: dict[str, Any] = {
        "echo": _bool_env("SQLALCHEMY_ECHO", False),
        "pool_pre_ping": _bool_env("SQLALCHEMY_POOL_PRE_PING", True),
        "pool_recycle": _int_env("SQLALCHEMY_POOL_RECYCLE", 1800),
        "future": True,
    }
    if _bool_env("ALEMBIC_DISABLE_POOL", False):
        opts["poolclass"] = pool.NullPool

    return create_async_engine(url, **opts)


# =============================================================================
# 🌐 Online миграции (sync/async)
# =============================================================================
def _run_migrations_sync(connection: Connection) -> None:
    logger.info("Connected (sync) to database: %s", connection.engine.url)
    context.configure(**make_context_kwargs(connection))
    with context.begin_transaction():
        context.run_migrations()


async def _run_migrations_async(async_engine: AsyncEngine) -> None:
    async with async_engine.connect() as connection:
        logger.info("Connected (async) to database: %s", connection.engine.url)
        await connection.run_sync(lambda conn: context.configure(**make_context_kwargs(conn)))
        await connection.run_sync(lambda conn: context.begin_transaction().__enter__())
        try:
            await connection.run_sync(lambda conn: context.run_migrations())
        finally:
            # Завершаем транзакцию корректно
            await connection.run_sync(
                lambda conn: context.get_context()._proxy._transaction.__exit__(None, None, None)
            )


def run_migrations_online() -> None:
    """
    Запуск миграций в online-режиме (с реальным подключением).
    Авто-режим: если URL async (postgresql+asyncpg) — используем async-вариант.
    """
    url = config.get_main_option("sqlalchemy.url")
    if not url:
        raise RuntimeError("sqlalchemy.url is not set for online migrations")

    is_async = _detect_async(url)

    try:
        if is_async:
            async_engine = _build_async_engine(url)
            try:
                asyncio.run(_run_migrations_async(async_engine))
            finally:
                try:
                    async_engine.sync_engine.dispose()
                except Exception:
                    pass
        else:
            engine = _build_sync_engine(url)
            try:
                with engine.connect() as connection:
                    _run_migrations_sync(connection)
            finally:
                try:
                    engine.dispose()
                except Exception:
                    pass
    except OperationalError as exc:
        logger.error("Database connection failed: %s", exc)
        raise


# =============================================================================
# ▶️ Точка входа
# =============================================================================
if context.is_offline_mode():
    logger.info("Running migrations in OFFLINE mode (timezone=%s)", DEFAULT_TZ)
    run_migrations_offline()
else:
    logger.info("Running migrations in ONLINE mode (timezone=%s)", DEFAULT_TZ)
    run_migrations_online()
