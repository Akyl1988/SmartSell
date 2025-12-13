# alembic/env.py
from __future__ import annotations

import asyncio
import logging
import os
import sys
from logging.config import fileConfig
from pathlib import Path
from typing import Any

from alembic import context
from sqlalchemy import create_engine, event, pool
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.engine.url import URL, make_url
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.exc import OperationalError

# =============================================================================
# 🧭 Путь до корня проекта (чтобы импортировать app.*)
# =============================================================================
THIS_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = THIS_DIR.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# =============================================================================
# ⚙️ Alembic config & логирование
# =============================================================================
config = context.config
if config.config_file_name:
    fileConfig(config.config_file_name)
logger = logging.getLogger("alembic.env")

# =============================================================================
# 📥 .env загрузка (тихо, если нет python-dotenv)
# =============================================================================
def _load_dotenv_if_present() -> None:
    try:
        from dotenv import load_dotenv  # type: ignore
    except Exception:
        return
    for candidate in (PROJECT_ROOT / ".env.local", PROJECT_ROOT / ".env"):
        if candidate.exists():
            load_dotenv(candidate, override=False)
            logger.info("Loaded environment from %s", candidate)

_load_dotenv_if_present()

# =============================================================================
# 🔍 Обнаружение окружения
# =============================================================================
def _is_testing() -> bool:
    if os.getenv("PYTEST_CURRENT_TEST"):
        return True
    env = (os.getenv("ENVIRONMENT") or os.getenv("PYTHON_ENV") or "").lower()
    return env in {"test", "testing"}

# =============================================================================
# 🔗 Получение URL БД
# =============================================================================
def _get_db_url_from_settings() -> str | None:
    """
    Пытаемся взять URL из pydantic-настроек приложения:
    app.core.config.get_settings().DATABASE_URL / TEST_DATABASE_URL.
    """
    try:
        from app.core.config import get_settings  # type: ignore

        s = get_settings()
        if _is_testing() and getattr(s, "TEST_DATABASE_URL", None):
            return str(s.TEST_DATABASE_URL)
        if getattr(s, "DATABASE_URL", None):
            return str(s.DATABASE_URL)
    except Exception as e:
        logger.debug("Skip importing app.core.config.get_settings(): %s", e)
    return None

def _get_database_url() -> str:
    # 1) Специальная переменная для Alembic
    url = (os.getenv("ALEMBIC_DATABASE_URL") or "").strip()
    if url:
        return url
    # 2) Тестовый URL, если запускаемся под pytest
    if _is_testing():
        url = (os.getenv("TEST_DATABASE_URL") or "").strip()
        if url:
            return url
    # 3) Обычный URL
    url = (os.getenv("DATABASE_URL") or "").strip()
    if url:
        return url
    # 4) Pydantic settings
    url = _get_db_url_from_settings()
    if url:
        return url
    # 5) sqlalchemy.url из alembic.ini
    url = (config.get_main_option("sqlalchemy.url") or "").strip()
    if url:
        return url
    raise RuntimeError(
        "DATABASE_URL is not set. "
        "Provide DATABASE_URL (or ALEMBIC_DATABASE_URL / TEST_DATABASE_URL) "
        "e.g. postgresql+psycopg2://user:pass@127.0.0.1:5432/dbname"
    )

DATABASE_URL = _get_database_url()
config.set_main_option("sqlalchemy.url", DATABASE_URL)

# =============================================================================
# 🗂️ Метаданные моделей
# =============================================================================
try:
    from app.database.base import Base  # type: ignore
except Exception as e:
    raise RuntimeError("Failed to import Base metadata from app.database.base") from e

target_metadata = Base.metadata

def _import_models_for_autogenerate() -> None:
    """Импортируем все модели, чтобы Alembic видел таблицы."""
    try:
        import app.models  # noqa: F401
        logger.info("Imported app.models for autogenerate")
    except Exception as e:
        logger.warning("Could not import app.models: %s", e)

_import_models_for_autogenerate()

# =============================================================================
# 🏷️ Именование, если не задано в Base
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
# 🔧 Хуки Alembic
# =============================================================================
def include_object(object: Any, name: str, type_: str, reflected: bool, compare_to: Any) -> bool:
    # Можно отфильтровать системные таблицы, если нужно
    # if type_ == "table" and name.startswith("pg_"):
    #     return False
    return True

def process_revision_directives(context_: Any, revision: Any, directives: list[Any]) -> None:
    """Удаляем пустые ревизии при autogenerate."""
    if getattr(config.cmd_opts, "autogenerate", False):
        script = directives[0]
        if script.upgrade_ops.is_empty():
            directives[:] = []
            logger.info("No schema changes detected; skipping empty revision.")

# =============================================================================
# ⚙️ Утилиты
# =============================================================================
def _is_sqlite(url: str) -> bool:
    try:
        return make_url(url).get_backend_name().startswith("sqlite")
    except Exception:
        return url.startswith("sqlite")

def _is_async_url(url: str) -> bool:
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

# =============================================================================
# 🔌 Настройки движков
# =============================================================================
def _engine_options_from_env() -> dict[str, Any]:
    """Опции пула и эха (для sync)."""
    opts: dict[str, Any] = {
        "pool_size": _int_env("SQLALCHEMY_POOL_SIZE", 5),
        "max_overflow": _int_env("SQLALCHEMY_MAX_OVERFLOW", 10),
        "pool_pre_ping": _bool_env("SQLALCHEMY_POOL_PRE_PING", True),
        "pool_recycle": _int_env("SQLALCHEMY_POOL_RECYCLE", 1800),
        "echo": _bool_env("SQLALCHEMY_ECHO", False),
        "future": True,
    }
    if _bool_env("ALEMBIC_DISABLE_POOL", False):
        opts["poolclass"] = pool.NullPool
    return opts

def _build_sync_engine(url: str) -> Engine:
    engine = create_engine(url, **_engine_options_from_env())
    if _is_sqlite(url):
        @event.listens_for(engine, "connect")
        def _set_sqlite_pragma(dbapi_conn, _):
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()
    return engine

def _build_async_engine(url: str) -> AsyncEngine:
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
# 🧵 OFFLINE
# =============================================================================
def run_migrations_offline() -> None:
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
        render_as_batch=True,  # безопаснее для оффлайна
        include_schemas=_bool_env("ALEMBIC_INCLUDE_SCHEMAS", False),
        version_table=os.getenv("ALEMBIC_VERSION_TABLE") or "alembic_version",
        version_table_schema=os.getenv("ALEMBIC_VERSION_TABLE_SCHEMA") or None,
    )

    with context.begin_transaction():
        context.run_migrations()

# =============================================================================
# 🌐 ONLINE (sync/async)
# =============================================================================
def _do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        include_object=include_object,
        process_revision_directives=process_revision_directives,
        compare_type=True,
        compare_server_default=True,
        render_as_batch=_is_sqlite(str(connection.engine.url)),
        include_schemas=_bool_env("ALEMBIC_INCLUDE_SCHEMAS", False),
        version_table=os.getenv("ALEMBIC_VERSION_TABLE") or "alembic_version",
        version_table_schema=os.getenv("ALEMBIC_VERSION_TABLE_SCHEMA") or None,
    )
    with context.begin_transaction():
        context.run_migrations()

async def _run_migrations_async(async_engine: AsyncEngine) -> None:
    async with async_engine.begin() as conn:
        logger.info("Connected (async) to %s", conn.engine.url)
        await conn.run_sync(_do_run_migrations)

def run_migrations_online() -> None:
    url = config.get_main_option("sqlalchemy.url")
    if not url:
        raise RuntimeError("sqlalchemy.url is not set for online migrations")

    try:
        if _is_async_url(url):
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
                    logger.info("Connected (sync) to %s", connection.engine.url)
                    _do_run_migrations(connection)
            finally:
                try:
                    engine.dispose()
                except Exception:
                    pass
    except OperationalError as exc:
        logger.error("Database connection failed: %s", exc)
        raise

# =============================================================================
# ▶️ Entry point
# =============================================================================
if context.is_offline_mode():
    logger.info("Running migrations in OFFLINE mode")
    run_migrations_offline()
else:
    logger.info("Running migrations in ONLINE mode")
    run_migrations_online()
