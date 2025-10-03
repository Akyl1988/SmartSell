from __future__ import annotations

import logging
import os
import sys
from logging.config import fileConfig
from pathlib import Path
from typing import Any

from sqlalchemy import engine_from_config, pool
from sqlalchemy.engine import Connection
from sqlalchemy.exc import OperationalError

from alembic import context

# =============================================================================
# 🧭 Поиск корня проекта и sys.path
# =============================================================================
# Корень проекта = директория, где лежит папка app/
# (оставляю вашу логику, дополняю более надёжной резолюцией путей)
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
# 📥 Загрузка .env (необязательно; нет зависимостей — тихо игнорируем)
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
            load_dotenv(
                dotenv_path=candidate, override=False
            )  # не перезаписываем уже выставленные env
            logger.info("Loaded environment from %s", candidate)


load_dotenv_silently()


# =============================================================================
# ⚙️ Получение DATABASE_URL
# =============================================================================
def is_testing_env() -> bool:
    """
    Определяем, запускаются ли миграции в тестовой среде.
    """
    if os.getenv("PYTEST_CURRENT_TEST"):
        return True
    env = (os.getenv("ENVIRONMENT") or os.getenv("PYTHON_ENV") or "").lower()
    return env in {"test", "testing"}


def _get_db_url_from_settings() -> str | None:
    """
    Пытаемся получить URL из pydantic-настроек приложения:
    app.core.config.get_settings().DATABASE_URL / TEST_DATABASE_URL
    Если импорт неудачен (например, из-за недостающих зависимостей) — возвращаем None.
    """
    try:
        # Импортируем аккуратно, чтобы не сломать миграции при отсутствии окружения
        from app.core.config import get_settings  # type: ignore

        s = get_settings()
        if not s:
            return None

        # Предпочтение тестовому при запуске под pytest
        if is_testing_env() and getattr(s, "TEST_DATABASE_URL", None):
            return str(s.TEST_DATABASE_URL)
        if getattr(s, "DATABASE_URL", None):
            return str(s.DATABASE_URL)
    except Exception as e:
        logger.debug("Skip settings import: %s", e)
    return None


def get_database_url() -> str:
    """
    Определяет SQLAlchemy URL для Alembic в приоритетном порядке:
    1) ALEMBIC_DATABASE_URL (если нужно принудительно переопределить)
    2) TEST_DATABASE_URL (если тестовая среда)
    3) DATABASE_URL (обычная среда)
    4) app.core.config.Settings (если удалось импортировать)
    5) sqlalchemy.url в alembic.ini
    """
    # 1) Явный оверрайд
    url = (os.getenv("ALEMBIC_DATABASE_URL") or "").strip()
    if url:
        return url

    # 2) Тестовая БД
    if is_testing_env():
        url = (os.getenv("TEST_DATABASE_URL") or "").strip()
        if url:
            return url

    # 3) Прод/дев БД
    url = (os.getenv("DATABASE_URL") or "").strip()
    if url:
        return url

    # 4) Попытка достать из pydantic Settings
    url = _get_db_url_from_settings()
    if url:
        return url.strip()

    # 5) Из alembic.ini
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
# Базовый объект метаданных
try:
    from app.database.base import Base  # noqa: E402
except Exception as e:
    raise RuntimeError("Failed to import Base metadata from app.database.base") from e

target_metadata = Base.metadata


# Пытаемся импортировать модели, чтобы autogenerate «увидел» все таблицы.
# Если ваш app/models/__init__.py подтягивает всё — одного импорта достаточно.
# Если нет — добавляйте сюда конкретные модули.
def try_import_models() -> None:
    try:
        import app.models  # noqa: F401

        logger.info("Imported app.models for autogenerate")
    except Exception as e:
        # Некритично — просто автоген может не увидеть все модели, если они нигде не импортированы.
        logger.warning("Could not import app.models: %s", e)


try_import_models()

# =============================================================================
# 🏷️ Naming convention (если не задано в Base.metadata)
# =============================================================================
if not getattr(target_metadata, "naming_convention", None):
    # Не меняем существующие схемы в ваших моделях — только если не задано
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
    # Пример: пропустить системные схемы/таблицы при необходимости
    # if type_ == "table" and name.startswith("pg_"):
    #     return False
    return True


def process_revision_directives(context_: Any, revision: Any, directives: list[Any]) -> None:
    """
    Убираем «пустые» миграции при autogenerate (чтобы не плодить пустые ревизии).
    """
    if getattr(config.cmd_opts, "autogenerate", False):
        script = directives[0]
        if script.upgrade_ops.is_empty():
            directives[:] = []
            logger.info("No schema changes detected; skipping empty revision.")


# =============================================================================
# 🧪 Сравнение типов/дефолтов
# =============================================================================
def make_context_kwargs(connection: Connection | None = None) -> dict[str, Any]:
    """
    Общие настройки для context.configure(...).
    """
    # Для SQLite удобно включать batch-режим; для Postgres можно выключить.
    render_as_batch = False
    try:
        url = str(connection.engine.url) if connection else config.get_main_option("sqlalchemy.url")
        if url and url.startswith("sqlite"):
            render_as_batch = True
    except Exception:
        pass

    return dict(
        connection=connection,
        target_metadata=target_metadata,
        include_object=include_object,
        process_revision_directives=process_revision_directives,
        compare_type=True,  # сравнивать типы колонок
        compare_server_default=True,  # сравнивать server_default (например, now(), gen_random_uuid())
        render_as_batch=render_as_batch,  # нужен для SQLite/ограничений в ALTER TABLE
        # include_schemas=True,          # включите при работе с несколькими схемами
        # version_table_schema="public", # если нужен отдельный schema для alembic_version
    )


# =============================================================================
# 🧵 Offline/Online миграции
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
        render_as_batch=True,  # offline-режим — безопаснее включить
    )

    with context.begin_transaction():
        context.run_migrations()


def _engine_options_from_env() -> dict[str, Any]:
    """
    Безопасно вычитываем опции пула/эха из окружения.
    """

    def _as_int(var: str, default: int) -> int:
        try:
            return int(os.getenv(var, default))
        except Exception:
            return default

    def _as_bool(var: str, default: bool) -> bool:
        val = os.getenv(var)
        if val is None:
            return default
        return str(val).lower() in {"1", "true", "yes", "y", "on"}

    return {
        "pool_size": _as_int("SQLALCHEMY_POOL_SIZE", 5),
        "max_overflow": _as_int("SQLALCHEMY_MAX_OVERFLOW", 10),
        "pool_pre_ping": _as_bool("SQLALCHEMY_POOL_PRE_PING", True),
        "pool_recycle": _as_int("SQLALCHEMY_POOL_RECYCLE", 1800),
        "echo": _as_bool("SQLALCHEMY_ECHO", False),
        # "poolclass": pool.QueuePool,   # по умолчанию; можно задать явно
    }


def run_migrations_online() -> None:
    """
    Запуск миграций в online-режиме (с реальным подключением).
    """
    ini_section = config.get_section(config.config_ini_section) or {}
    ini_section = dict(ini_section)  # копия, чтобы можно было добавлять опции

    # Принудительно выставляем URL (мог измениться при вычислении выше)
    ini_section["sqlalchemy.url"] = config.get_main_option("sqlalchemy.url")

    # Формируем engine с современными опциями (future=True для SQLAlchemy 1.4+/2.0)
    connectable = engine_from_config(
        ini_section,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool
        if os.getenv("ALEMBIC_DISABLE_POOL")
        else None,  # можно выключить пул для миграций
        future=True,
    )

    # Применяем дополнительные опции через raw connection если нужно —
    # но engine_from_config уже их учитывает при наличии в ini_section.
    # Альтернативно можно было бы собирать create_engine(...) вручную,
    # однако оставляю совместимым со стандартным alembic.ini.

    try:
        with connectable.connect() as connection:
            logger.info("Connected to database: %s", connection.engine.url)
            context.configure(**make_context_kwargs(connection))

            with context.begin_transaction():
                context.run_migrations()
    except OperationalError as exc:
        logger.error("Database connection failed: %s", exc)
        raise
    finally:
        try:
            connectable.dispose()
        except Exception:
            pass


# =============================================================================
# ▶️ Точка входа
# =============================================================================
if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
