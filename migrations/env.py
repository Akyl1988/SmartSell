# ======================================================================
#  ФАЙЛ: migrations/env.py
#  НАЗНАЧЕНИЕ: боевой Alembic-скрипт для автогенерации и применения
#  миграций «по-взрослому». Кодировка — UTF-8, переводы строк — LF.
# ======================================================================

from __future__ import annotations

"""
Что умеет и чем полезен:
- Берёт DATABASE_URL из окружения (с нормализацией под postgresql://).
- Гарантированно импортирует модели, чтобы autogenerate видел все таблицы.
- Поддерживает несколько Base/metadata с объединением в одну целевую MetaData.
- Работает в online/offline режимах; использует транзакционный DDL на PostgreSQL.
- Включает compare_type и compare_server_default для корректного диффа.
- Гибко исключает системные объекты и свои таблицы (include_object/include_name/include_schemas).
- Не создаёт пустые миграции (process_revision_directives).
- Автоматически фильтрует «случайные» операции над таблицей версий Alembic.
- Защитный режим: по умолчанию блокирует деструктивные операции (DROP ...),
  пока не включён ALLOW_DROPS=1.
- Аккуратно ведёт себя на SQLite (render_as_batch, PRAGMA foreign_keys).
- Поддерживает version_table_schema='public' на PostgreSQL.
- Добавляет naming_convention, если у целевой MetaData она не задана (для стабильных диффов).
- Поддерживает schema_translate_map через переменные окружения.
- Явно выставляет search_path на время миграций: public,"$user" (без сюрпризов со схемой пользователя).
- Позволяет включить жёсткий whitelist схем через ALEMBIC_INCLUDE_SCHEMAS.
- Позволяет задать DEFAULT_SCHEMA (по умолчанию 'public') — для генерации объектов.
- Если присутствует python-dotenv, подхватывает переменные из .env (не обязательно).

Требования к окружению (любые из них):
- DATABASE_URL / DB_URL / SQLALCHEMY_DATABASE_URI — строка подключения.
- alembic.ini -> sqlalchemy.url — резервный URL (используется, если переменные не заданы).

Поддерживаемые формы URL:
- postgresql://user:pass@host:5432/db
- postgresql+psycopg2://... (будет нормализован к postgresql://)
- sqlite:///path/to.db  (в этом случае включается render_as_batch)
"""

import importlib
import os
import re
import sys
import warnings
from collections.abc import Iterable
from logging import getLogger
from logging.config import fileConfig
from pathlib import Path
from typing import Any, Optional

# --- Alembic autogenerate filters (import-safe) ---
_MIGRATIONS_DIR = Path(__file__).resolve().parent
if str(_MIGRATIONS_DIR) not in sys.path:
    sys.path.insert(0, str(_MIGRATIONS_DIR))
from alembic_autogen_filters import build_target_table_ids, include_object_filter as core_filter  # noqa: E402

# --- .env (опционально) -------------------------------------------------
try:
    from dotenv import load_dotenv  # type: ignore
except Exception:  # pragma: no cover
    load_dotenv = None  # type: ignore

if load_dotenv:
    # .env приоритет ниже реальных переменных среды — стандартно.
    load_dotenv()

# --- Alembic / SQLAlchemy -----------------------------------------------
import sqlalchemy as sa
from alembic import context
from alembic.config import Config
from alembic.runtime.environment import EnvironmentContext
from sqlalchemy import MetaData, create_engine, text
from sqlalchemy import schema as sa_schema  # noqa: F401  (зарезервировано на будущее)
from sqlalchemy.engine import Connection
from sqlalchemy.engine.url import make_url

# SmartSell config
try:
    from app.core.config import db_connection_fingerprint, get_settings
except Exception:  # pragma: no cover
    get_settings = None  # type: ignore
    db_connection_fingerprint = None  # type: ignore

try:
    from app.core.alembic_autogen import include_object as alembic_include_object
except Exception:  # pragma: no cover
    alembic_include_object = None  # type: ignore

logger = getLogger(__name__)

# Операции для пост-обработки автогенерации
try:
    from alembic.operations.ops import (  # type: ignore
        DowngradeOps,
        DropColumnOp,
        DropConstraintOp,
        DropIndexOp,
        DropTableOp,
        MigrateOperation,
        UpgradeOps,
    )
except Exception:
    # Совместимость со старыми версиями Alembic — не валимся
    UpgradeOps = DowngradeOps = MigrateOperation = object  # type: ignore
    DropTableOp = DropColumnOp = DropConstraintOp = DropIndexOp = None  # type: ignore

# ======================================================================
# 1) Базовая конфигурация из alembic.ini
# ======================================================================

config: Config = context.config

# Логирование Alembic из alembic.ini
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# ======================================================================
# 2) Утилиты и флаги
# ======================================================================


def _debug(msg: str) -> None:
    """Лёгкий дебаг в консоль Alembic (включается ALEMBIC_DEBUG=1)."""
    if os.getenv("ALEMBIC_DEBUG", "0").lower() in ("1", "true", "yes", "y"):
        print(f"[alembic-debug] {msg}")


def _get_bool_env(name: str, default: bool = False) -> bool:
    val = os.getenv(name)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "y")


def _is_sqlite_url(url: str) -> bool:
    try:
        return make_url(url).get_backend_name() == "sqlite"
    except Exception:
        return False


def _is_postgres_url(url: str) -> bool:
    try:
        return make_url(url).get_backend_name() in ("postgresql", "postgres")
    except Exception:
        return False


def normalize_db_url(raw: Optional[str]) -> str:
    """
    Нормализуем URL:
    - Меняем 'postgresql+psycopg2' -> 'postgresql'
    - Убираем '?sslmode=...' в конце (если есть)
    - Валидируем через make_url; при ошибке — дефолт.
    """
    default_url = os.getenv(
        "ALEMBIC_DEFAULT_URL",
        "postgresql://postgres@127.0.0.1:5432/smartsell",
    )
    if not raw:
        return default_url

    url = raw.strip()
    url = url.replace("postgresql+psycopg2", "postgresql")
    url = re.sub(r"\?sslmode=\w+$", "", url)

    try:
        make_url(url)
    except Exception:
        _debug(f"URL '{url}' не прошёл make_url(); используем дефолт.")
        url = default_url
    return url


def _project_root_to_sys_path() -> None:
    """Добавляем корень проекта в sys.path для корректных import app.*"""
    root = os.path.abspath(os.getcwd())
    if root not in sys.path:
        sys.path.insert(0, root)


def _import_attr(dotted: str):
    """
    Импорт по строке вида 'package.module:attr'. Возвращает attr или None.
    """
    mod_name, _, attr = dotted.partition(":")
    try:
        mod = importlib.import_module(mod_name)
        return getattr(mod, attr) if attr else mod
    except Exception as e:
        _debug(f"Импорт не удался: {dotted!r} -> {e!r}")
        return None


def _collect_bases() -> list[tuple[MetaData, str]]:
    """
    Собираем MetaData из возможных баз проекта (несколько Base допустимо).
    Возвращаем список пар (metadata, source).
    """
    _project_root_to_sys_path()

    # Кандидаты для поиска декларативной базы
    # ПРИОРИТЕТ: app.models.base.Base — единственный источник истины
    base_candidates = [
        "app.models.base:Base",  # ОСНОВНОЙ источник (единственный DeclarativeBase)
        "app.models:Base",  # реэкспорт из __init__
        "app.core.db:Base",  # fallback импорт (должен быть тот же Base)
    ]

    # Явно вызываем ensure_models_loaded() для загрузки всех моделей
    try:
        from app.models import ensure_models_loaded

        ensure_models_loaded()
        _debug("Модели загружены через ensure_models_loaded()")
    except Exception as e:
        _debug(f"ensure_models_loaded() не удалось: {e!r}")
        # Fallback: прямой импорт критичных модулей
        for pkg in (
            "app.models",
            "app.models.base",
            "app.models.user",
            "app.models.company",
            "app.models.product",
        ):
            try:
                importlib.import_module(pkg)
            except Exception:
                pass

    metas: list[tuple[MetaData, str]] = []

    for dotted in base_candidates:
        Base = _import_attr(dotted)
        if Base is None:
            continue
        md = getattr(Base, "metadata", None)
        if isinstance(md, MetaData):
            metas.append((md, dotted))
            _debug(f"Найдена MetaData из {dotted}")
    return metas


def _ensure_naming_convention(md: MetaData) -> None:
    """
    Если у MetaData нет naming_convention — зададим её для стабильных имён
    индексов/ограничений (важно для корректных диффов и даунгрейдов).
    Синхронизировано с app.models.base.NAMING_CONVENTIONS.
    """
    if getattr(md, "naming_convention", None):
        return
    md.naming_convention = {
        "ix": "ix__%(table_name)s__%(column_0_N_name)s",
        "uq": "uq__%(table_name)s__%(column_0_N_name)s",
        "ck": "ck__%(table_name)s__%(constraint_name)s",
        "fk": "fk__%(table_name)s__%(column_0_N_name)s__%(referred_table_name)s",
        "pk": "pk__%(table_name)s",
    }


def _combine_metadata(metas: Iterable[tuple[MetaData, str]]) -> tuple[MetaData | None, list[str]]:
    """
    Объединяем несколько MetaData в одну целевую (для autogenerate).
    """
    metas = list(metas)
    if not metas:
        return None, []

    if len(metas) == 1:
        md, src = metas[0]
        _ensure_naming_convention(md)
        return md, [src]

    target = MetaData()
    _ensure_naming_convention(target)
    sources: list[str] = []

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        for md, source in metas:
            _ensure_naming_convention(md)
            for t in md.tables.values():  # без .sorted_tables — меньше шума про циклы
                t.tometadata(target)
            sources.append(source)
    _debug(f"Скомбинирована целевая MetaData из: {', '.join(sources)}")
    return target, sources


def get_url_from_env_or_cfg() -> str:
    """
    Порядок приоритета (enterprise-safe):
    1) app.core.config.get_settings().sqlalchemy_sync_url (respects TESTING/TEST_* env)
    2) fallback: env DATABASE_URL / DB_URL / SQLALCHEMY_DATABASE_URI
    3) fallback: alembic.ini sqlalchemy.url
    """
    ini_url = config.get_main_option("sqlalchemy.url")
    env_url = os.getenv("DATABASE_URL") or os.getenv("DB_URL") or os.getenv("SQLALCHEMY_DATABASE_URI")

    source = "env-or-ini"
    url = None

    if get_settings:
        try:
            settings = get_settings()
            url = settings.sqlalchemy_sync_url
            source = settings.db_url_source() if hasattr(settings, "db_url_source") else "settings"
            safe = getattr(settings, "db_url_safe", "")
            fp = db_connection_fingerprint(url or "", include_password=False) if db_connection_fingerprint else ""
            logger.info("alembic_db_url_resolved source=%s url=%s fp=%s", source, safe, fp)
        except Exception as e:  # pragma: no cover
            logger.warning("get_settings() failed in Alembic env: %s", e)
            url = None

    if not url:
        url = env_url or ini_url
        source = "env-fallback" if env_url else "ini"
        safe = url or ""
        try:
            fp = db_connection_fingerprint(url or "", include_password=False) if db_connection_fingerprint else ""
        except Exception:
            fp = ""
        logger.info("alembic_db_url_resolved source=%s url=%s fp=%s", source, safe, fp)

    return normalize_db_url(url)


# ======================================================================
# 3) Target Metadata (для autogenerate)
# ======================================================================

_found_metas = _collect_bases()
# Явно используем единую MetaData из app.models.base.Base
try:
    from app.models.base import Base as _ModelsBase  # единый DeclarativeBase

    target_metadata = _ModelsBase.metadata
    _meta_sources = ["app.models.base:Base"]
    _debug("Target metadata set to app.models.base.Base.metadata")
except Exception:
    # Fallback: комбинируем найденные метаданные (на случай ранней загрузки)
    target_metadata, _meta_sources = _combine_metadata(_found_metas)

if target_metadata is None:
    _debug("ВНИМАНИЕ: ни одна MetaData не найдена. " "Autogenerate не сможет сравнить модели со схемой БД.")

# Extract target table IDs from ORM metadata for autogenerate filtering
# Format: set of (schema, tablename) tuples for all tables declared in ORM models
target_table_ids: set[tuple[str | None, str]] = set()
if target_metadata:

    target_table_ids = build_target_table_ids(target_metadata)
    _debug(f"Target table IDs extracted: {len(target_table_ids)} tables")

# ======================================================================
# 4) Политика включения/исключения объектов
# ======================================================================

SYSTEM_TABLE_PREFIXES = (
    "pg_",  # PostgreSQL системные
    "sqlalchemy_",  # служебные таблицы SQLAlchemy
)

# Таблица версий Alembic — имя и схема. Мы исключим её из автогенерации.
ALEMBIC_VERSION_TABLE = os.getenv("ALEMBIC_VERSION_TABLE", "alembic_version")
ALEMBIC_VERSION_TABLE_SCHEMA = os.getenv("ALEMBIC_VERSION_TABLE_SCHEMA") or None

EXCLUDE_TABLES = set(t.strip() for t in os.getenv("ALEMBIC_EXCLUDE_TABLES", "").split(",") if t.strip())

EXCLUDE_SCHEMAS = set(s.strip() for s in os.getenv("ALEMBIC_EXCLUDE_SCHEMAS", "").split(",") if s.strip())
INCLUDE_SCHEMAS_OPT = os.getenv("ALEMBIC_INCLUDE_SCHEMAS")  # явный whitelist

DEFAULT_SCHEMA = os.getenv("DEFAULT_SCHEMA", "public").strip() or "public"


# Wrapper to use imported include_object or fallback if import fails
if alembic_include_object:
    include_object = alembic_include_object
else:
    # Fallback implementation (same as app.core.alembic_autogen)
    def include_object(obj, name: str, type_: str, reflected: bool, compare_to):
        """
        Alembic autogenerate guard (fallback).

        Filters objects to include in autogenerated migrations:
        - If an object is reflected from the database but has no counterpart in
          ORM metadata (compare_to is None), it is DB-only / Core-only and must
          be ignored to prevent destructive drop_* suggestions.
        """
        if reflected and compare_to is None:
            return False
        return True


def include_object_with_system_filters(object_, name: str, type_: str, reflected: bool, compare_to):
    """
    Extended filter that combines app.core.alembic_autogen.include_object with project-specific
    system table and schema filters, plus table ID validation.

    Rules:
    - Tables: Allow alembic_version; otherwise only include if (schema, name) is in target_table_ids
    - Indexes/constraints: Only include if parent table is in target_table_ids
    - Everything else: Include by default
    """

    # Use core filter for DB-only detection and table ID checking
    if not core_filter(object_, name, type_, reflected, compare_to, target_table_ids, ALEMBIC_VERSION_TABLE):
        return False

    # Additional checks: existing filters (system prefixes, exclude lists, schemas)
    schema_name = getattr(getattr(object_, "schema", None), "name", None) or getattr(object_, "schema", None)
    if schema_name and schema_name in EXCLUDE_SCHEMAS:
        return False

    if INCLUDE_SCHEMAS_OPT:
        include_whitelist = set(s.strip() for s in INCLUDE_SCHEMAS_OPT.split(",") if s.strip())
        # Если whitelist задан — берём только указанные схемы
        if schema_name and schema_name not in include_whitelist:
            return False

    if type_ == "table":
        if any(name.startswith(pref) for pref in SYSTEM_TABLE_PREFIXES):
            return False
        if name in EXCLUDE_TABLES:
            return False

    if type_ in ("view", "materialized_view"):
        return False

    # All other types: included
    return True


def include_name(name: str | None, type_: str | None, parent_names) -> bool:
    """
    Доп. фильтрация по имени (используется Alembic для некоторых объектов).
    """
    if not name:
        return True
    if type_ == "table" and any(name.startswith(pref) for pref in SYSTEM_TABLE_PREFIXES):
        return False
    return True


def _ensure_version_table_size(connection: Connection, schema: str | None) -> None:
    """Ensure alembic_version.version_num can store long revision ids (256 chars) if table already exists."""
    vt_schema = schema or DEFAULT_SCHEMA or "public"
    try:
        sql = f"""
                DO $$
                BEGIN
                    IF EXISTS (
                        SELECT 1 FROM information_schema.columns
                         WHERE table_schema='{vt_schema}'
                             AND table_name='{ALEMBIC_VERSION_TABLE}'
                             AND column_name='version_num'
                             AND (character_maximum_length < 256 OR character_maximum_length IS NULL)
                    ) THEN
                        EXECUTE 'ALTER TABLE "{vt_schema}"."{ALEMBIC_VERSION_TABLE}" ALTER COLUMN version_num TYPE VARCHAR(256)';
                    END IF;
                END$$;
                """
        connection.execute(text(sql))
    except Exception as e:  # pragma: no cover - diagnostic only
        _debug(f"Version table size check skipped: {e!r}")


# ======================================================================
# 5) Защита от пустых/опасных миграций и случайных операций
# ======================================================================

ALLOW_DROPS = _get_bool_env("ALLOW_DROPS", False)  # включить, чтобы разрешить DROP-операции


def _strip_accidental_version_table_ops(script) -> None:
    """
    Удаляем любые действия над таблицей версий Alembic.
    """
    if not getattr(script, "upgrade_ops", None):
        return

    def _filter_ops(container_ops):
        kept = []
        for op in list(container_ops):
            nested = getattr(op, "ops", None)
            if isinstance(nested, list):
                _filter_ops(nested)
                kept.append(op)
                continue

            if DropTableOp and isinstance(op, DropTableOp):
                tbl_name = getattr(op, "table_name", None)
                schema = getattr(op, "schema", None)
                if tbl_name == ALEMBIC_VERSION_TABLE and (
                    schema == ALEMBIC_VERSION_TABLE_SCHEMA or ALEMBIC_VERSION_TABLE_SCHEMA is None
                ):
                    _debug("Удалена случайная операция DropTable над alembic_version.")
                    continue
            kept.append(op)

        container_ops[:] = kept

    _filter_ops(script.upgrade_ops.ops)


def _remove_destructive_ops_if_not_allowed(script) -> None:
    """
    Если ALLOW_DROPS не включён — удаляем деструктивные операции:
    DropTableOp, DropColumnOp, DropConstraintOp, DropIndexOp.
    """
    if ALLOW_DROPS or not getattr(script, "upgrade_ops", None):
        return

    removed: list[str] = []

    def _filter_ops(container_ops):
        kept = []
        for op in list(container_ops):
            nested = getattr(op, "ops", None)
            if isinstance(nested, list):
                _filter_ops(nested)
                kept.append(op)
                continue

            to_remove = (
                (DropTableOp and isinstance(op, DropTableOp))
                or (DropColumnOp and isinstance(op, DropColumnOp))
                or (DropConstraintOp and isinstance(op, DropConstraintOp))
                or (DropIndexOp and isinstance(op, DropIndexOp))
            )

            if to_remove:
                removed.append(type(op).__name__)
                continue

            kept.append(op)

        container_ops[:] = kept

    _filter_ops(script.upgrade_ops.ops)
    if removed:
        _debug(f"Удалены потенциально опасные операции (ALLOW_DROPS=0): {', '.join(removed)}")


def process_revision_directives(
    context_: EnvironmentContext,
    revision: str,
    directives: list,
) -> None:
    """
    • Если изменений нет — не создаём пустую ревизию.
    • Очищаем любые случайные операции над таблицей версий alembic_version.
    • Фильтруем деструктивные операции, если ALLOW_DROPS выключен.
    """
    if not directives:
        return
    script = directives[0]

    if getattr(script, "upgrade_ops", None) and script.upgrade_ops.is_empty():
        _debug("Autogenerate не нашёл изменений — пустая миграция отменена.")
        directives[:] = []
        return

    _strip_accidental_version_table_ops(script)
    _remove_destructive_ops_if_not_allowed(script)


# ======================================================================
# 6) Конфигурация Alembic context
# ======================================================================


def _schema_translate_map() -> dict[str, str] | None:
    """
    Позволяет подменять схему на лету (удобно для тестов или мульти-tenant).
    Пример:
      SCHEMA_TRANSLATE_FROM=public
      SCHEMA_TRANSLATE_TO=test_schema
    """
    src = os.getenv("SCHEMA_TRANSLATE_FROM")
    dst = os.getenv("SCHEMA_TRANSLATE_TO")
    if src and dst:
        return {src: dst}
    return None


def _configure_context(connection: Connection | None = None) -> None:
    """
    Общая настройка Alembic context для online/offline.
    """
    url = get_url_from_env_or_cfg()
    render_as_batch = _is_sqlite_url(url)

    # Если это PostgreSQL и схема для таблицы версий не указана,
    # по умолчанию фиксируем 'public' (чтобы не было сюрпризов).
    version_table_schema = ALEMBIC_VERSION_TABLE_SCHEMA
    if version_table_schema is None and _is_postgres_url(url):
        version_table_schema = DEFAULT_SCHEMA or "public"

    include_schemas_cfg = config.get_main_option("include_schemas", "true").lower() in (
        "1",
        "true",
        "yes",
    )

    opts: dict[str, Any] = dict(
        target_metadata=target_metadata,
        include_object=include_object_with_system_filters,
        include_name=include_name,
        process_revision_directives=process_revision_directives,
        compare_type=True,
        compare_server_default=True,
        render_as_batch=render_as_batch,
        literal_binds=True if context.is_offline_mode() else False,
        dialect_opts={"paramstyle": "named"},
        transaction_per_migration=True,
        # Фиксация таблицы версий
        version_table=ALEMBIC_VERSION_TABLE,
        version_table_schema=version_table_schema,
        version_table_column_type=sa.String(length=256),
        # Схемы включаем — важно для сложных проектов
        include_schemas=include_schemas_cfg,
    )

    stm = _schema_translate_map()
    if stm:
        opts["schema_translate_map"] = stm

    if connection is not None:
        context.configure(connection=connection, **opts)
    else:
        context.configure(url=url, **opts)


# ======================================================================
# 7) Offline / Online
# ======================================================================


def _session_set_search_path(conn: Connection, default_schema: str = "public") -> None:
    """
    На время миграций жёстко выставляем search_path: default_schema, "$user".
    Это исключает ситуацию, когда объекты улетают в схему пользователя (postgres).
    """
    try:
        conn.execute(text(f'SET search_path = "{default_schema}", "$user";'))
    except Exception as e:
        _debug(f"Не удалось выставить search_path: {e!r}")


def _ensure_alembic_version_text(conn: Connection) -> None:
    """Ensure public.alembic_version exists and version_num is TEXT."""
    try:
        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS public.alembic_version (
                    version_num text NOT NULL,
                    CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num)
                );
                """
            )
        )
    except Exception as e:
        _debug(f"Ensure alembic_version table skipped: {e!r}")

    try:
        conn.execute(text("ALTER TABLE public.alembic_version ALTER COLUMN version_num TYPE text;"))
    except Exception as e:
        _debug(f"Ensure alembic_version.version_num TYPE text skipped: {e!r}")


def run_migrations_offline() -> None:
    """
    Offline — эмитим SQL в stdout/файл без подключения к БД.
    """
    _configure_context(connection=None)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """
    Online — подключаемся к БД и выполняем миграции.
    """
    sqlalchemy_section = dict(config.get_section(config.config_ini_section) or {})
    sqlalchemy_section["sqlalchemy.url"] = get_url_from_env_or_cfg()
    url = sqlalchemy_section["sqlalchemy.url"]

    # Сессию создаём через create_engine (engine_from_config устаревающий путь)
    connectable = create_engine(url, future=True)

    try:
        if hasattr(connectable, "connect"):
            connection = connectable.connect()
            close_connection = True
        else:
            connection = connectable
            close_connection = False
        try:
            # Подготовка соединения в короткой транзакции, чтобы не держать долгий snapshot
            try:
                trans = None
                try:
                    begin = getattr(connection, "begin", None)
                    if callable(begin):
                        trans = begin()

                    # SQLite: включаем foreign_keys (на всякий случай)
                    try:
                        if _is_sqlite_url(url):
                            connection.execute(text("PRAGMA foreign_keys=ON"))
                    except Exception:
                        pass

                    # Диагностика подключения (без фатала при ошибке)
                    try:
                        if _is_postgres_url(url):
                            info = connection.execute(
                                text("select current_database(), current_user, current_schema()")
                            ).fetchone()
                            sp = connection.execute(text("show search_path")).scalar_one()
                            print(
                                f"[alembic] DB={info[0]} USER={info[1]} SCHEMA={info[2]} SEARCH_PATH={sp}"
                            )
                        elif _is_sqlite_url(url):
                            dbfile = url.replace("sqlite:///", "")
                            print(f"[alembic] SQLite DB file: {dbfile}")
                    except Exception as e:
                        print(f"[alembic] warn: cannot fetch connection info: {e}")

                    # Критично: зафиксировать search_path в сессии на public,"$user"
                    _session_set_search_path(connection, DEFAULT_SCHEMA)

                    if _is_postgres_url(url):
                        _ensure_alembic_version_text(connection)

                    # Базовая конфигурация контекста
                    version_table_schema = ALEMBIC_VERSION_TABLE_SCHEMA
                    if version_table_schema is None and _is_postgres_url(url):
                        version_table_schema = DEFAULT_SCHEMA or "public"

                    if _is_postgres_url(url):
                        connection.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{DEFAULT_SCHEMA}";'))
                        connection.execute(
                            text(
                                f"""
                                DO $$
                                BEGIN
                                    IF NOT EXISTS (
                                        SELECT 1
                                          FROM information_schema.tables
                                         WHERE table_schema = '{DEFAULT_SCHEMA}'
                                           AND table_name = '{ALEMBIC_VERSION_TABLE}'
                                    ) THEN
                                        NULL;
                                    END IF;
                                END$$;
                                """
                            )
                        )
                        _ensure_version_table_size(connection, version_table_schema)

                    if trans is not None and hasattr(trans, "commit"):
                        trans.commit()
                except Exception:
                    if trans is not None and hasattr(trans, "rollback"):
                        trans.rollback()
                    raise
            except Exception as e:
                _debug(f"Проверка/создание схемы или version table пропущены: {e!r}")

            _configure_context(connection=connection)

            with context.begin_transaction():
                context.run_migrations()
        finally:
            if close_connection:
                try:
                    connection.close()
                except Exception:
                    pass
    finally:
        connectable.dispose()


# ======================================================================
# 8) Точка входа
# ======================================================================

if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
