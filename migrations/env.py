# ======================================================================
#  ФАЙЛ: migrations/env.py
#  НАЗНАЧЕНИЕ: боевой Alembic-скрипт для автогенерации и применения
#  миграций «по-взрослому». Кодировка — UTF-8, переводы строк — LF.
# ======================================================================

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
- Если присутствует python-dotenv, подхватывает переменные из .env (не обязательно).

Требования к окружению (любые из них):
- DATABASE_URL / DB_URL / SQLALCHEMY_DATABASE_URI — строка подключения.
- alembic.ini -> sqlalchemy.url — резервный URL (используется, если переменные не заданы).

Поддерживаемые формы URL:
- postgresql://user:pass@host:5432/db
- postgresql+psycopg2://... (будет нормализован к postgresql://)
- sqlite:///path/to.db  (в этом случае включается render_as_batch)
"""

from __future__ import annotations

import importlib
import os
import re
import sys
import warnings
from collections.abc import Iterable
from logging.config import fileConfig
from typing import Any, Optional

# --- .env (опционально) -------------------------------------------------
# Подхватываем .env, если установлен python-dotenv. Не критично, если нет.
try:
    from dotenv import load_dotenv  # type: ignore
except Exception:  # pragma: no cover
    load_dotenv = None  # type: ignore

if load_dotenv:
    # .env приоритетен ниже «реальных» переменных среды — это норма.
    # Если файл большой, можно ограничить override=False (по умолчанию False).
    load_dotenv()

# В проекте встречается алиас
# --- Alembic / SQLAlchemy -----------------------------------------------
from sqlalchemy import MetaData, engine_from_config
from sqlalchemy import schema as sa_schema  # noqa: F401  (оставлено на будущее)
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.engine.url import make_url
from sqlalchemy.exc import OperationalError, ProgrammingError

from alembic import context
from alembic.runtime.environment import EnvironmentContext

# Попытка импортировать классы операций для пост-обработки автогенерации
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
    # Совместимость со старыми версиями Alembic — не заваливаемся
    UpgradeOps = DowngradeOps = MigrateOperation = object  # type: ignore
    DropTableOp = DropColumnOp = DropConstraintOp = DropIndexOp = None  # type: ignore

# ======================================================================
# 1) Базовая конфигурация из alembic.ini
# ======================================================================

config = context.config

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
    default_url = "postgresql://postgres:admin123@localhost:5432/smartsell2"
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

    # Кандидаты для поисков декларативной базы
    base_candidates = [
        "app.core.db:Base",
        "app.models:Base",
        "app.db.base:Base",
        "app.db.models:Base",
        "app.database:Base",
        "models:Base",
    ]

    metas: list[tuple[MetaData, str]] = []

    # Дополнительно: импортируем пакет app.models, если он регистрирует модели побочно
    try:
        importlib.import_module("app.models")
    except Exception:
        pass

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
    """
    if getattr(md, "naming_convention", None):
        return
    md.naming_convention = {
        "ix": "ix_%(table_name)s_%(column_0_label)s",
        "uq": "uq_%(table_name)s_%(column_0_name)s",
        "ck": "ck_%(table_name)s_%(constraint_name)s",
        "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
        "pk": "pk_%(table_name)s",
    }


def _combine_metadata(
    metas: Iterable[tuple[MetaData, str]]
) -> tuple[Optional[MetaData], list[str]]:
    """
    Объединяем несколько MetaData в одну целевую (для autogenerate).
    ВАЖНО: не используем .sorted_tables, чтобы не ловить SAWarning о циклах —
    переносим таблицы в произвольном порядке.
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

    # Игнорируем предупреждения о циклах зависимостей
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        for md, source in metas:
            _ensure_naming_convention(md)
            for t in md.tables.values():  # ключевой фикс против SAWarning
                t.tometadata(target)
            sources.append(source)
    _debug(f"Скомбинирована целевая MetaData из: {', '.join(sources)}")
    return target, sources


def get_url_from_env_or_cfg() -> str:
    """
    Порядок приоритета:
    1) alembic.ini -> sqlalchemy.url
    2) ENV: DATABASE_URL / DB_URL / SQLALCHEMY_DATABASE_URI
    3) дефолт (localhost).
    """
    ini_url = config.get_main_option("sqlalchemy.url")
    env_url = (
        os.getenv("DATABASE_URL") or os.getenv("DB_URL") or os.getenv("SQLALCHEMY_DATABASE_URI")
    )
    return normalize_db_url(ini_url or env_url)


# ======================================================================
# 3) Target Metadata (для autogenerate)
# ======================================================================

_found_metas = _collect_bases()
target_metadata, _meta_sources = _combine_metadata(_found_metas)

if target_metadata is None:
    _debug(
        "ВНИМАНИЕ: ни одна MetaData не найдена. "
        "Autogenerate не сможет сравнить модели со схемой БД."
    )

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

EXCLUDE_TABLES = set(
    t.strip() for t in os.getenv("ALEMBIC_EXCLUDE_TABLES", "").split(",") if t.strip()
)
# Пример: ALEMBIC_EXCLUDE_TABLES="audit_view,materialized_stats"

EXCLUDE_SCHEMAS = set(
    s.strip() for s in os.getenv("ALEMBIC_EXCLUDE_SCHEMAS", "").split(",") if s.strip()
)
INCLUDE_SCHEMAS_OPT = os.getenv("ALEMBIC_INCLUDE_SCHEMAS")  # если нужно задать явный whitelist


def include_object(object_, name: str, type_: str, reflected: bool, compare_to):
    """
    True — включаем объект в миграцию.
    - Исключаем системные таблицы и явно перечисленные.
    - Исключаем VIEW/матвью по умолчанию (обычно создаются вручную SQL).
    - Исключаем таблицу версий Alembic из autogenerate.
    - Поддерживаем исключение целых схем через ALEMBIC_EXCLUDE_SCHEMAS.
    """
    # Исключаем таблицу версий Alembic (name без схемы)
    if type_ == "table" and name == ALEMBIC_VERSION_TABLE:
        return False

    # Схема объекта (если есть)
    schema_name = getattr(getattr(object_, "schema", None), "name", None) or getattr(
        object_, "schema", None
    )
    if schema_name and schema_name in EXCLUDE_SCHEMAS:
        return False

    # Явный whitelist схем (если задан)
    if INCLUDE_SCHEMAS_OPT:
        include_whitelist = set(s.strip() for s in INCLUDE_SCHEMAS_OPT.split(",") if s.strip())
        if schema_name and schema_name not in include_whitelist:
            return False

    if type_ == "table":
        # системные
        if any(name.startswith(pref) for pref in SYSTEM_TABLE_PREFIXES):
            return False
        # явные исключения
        if name in EXCLUDE_TABLES:
            return False
        return True

    if type_ in ("view", "materialized_view"):
        return False

    # Индексы/колонки/констрейнты — по умолчанию включаем
    return True


def include_name(name: Optional[str], type_: Optional[str], parent_names) -> bool:
    """
    Доп. фильтрация по имени (используется Alembic для некоторых объектов).
    Здесь оставляем максимально либерально, исключая только системные префиксы.
    """
    if not name:
        return True
    if type_ == "table" and any(name.startswith(pref) for pref in SYSTEM_TABLE_PREFIXES):
        return False
    return True


# ======================================================================
# 5) Защита от пустых/опасных миграций и случайных операций
# ======================================================================

ALLOW_DROPS = _get_bool_env("ALLOW_DROPS", False)  # включить, чтобы разрешить DROP-операции


def _strip_accidental_version_table_ops(script) -> None:
    """
    Из сгенерированных операций удаляем любые действия над таблицей версий
    Alembic (например, DropTableOp('alembic_version')), если таковые вдруг
    попали в автогенерацию (это может случиться при смене схемы version_table).
    """
    if not getattr(script, "upgrade_ops", None):
        return

    def _filter_ops(container_ops):
        kept = []
        for op in list(container_ops):
            # Рекурсивная обработка вложенных контейнеров (UpgradeOps/DowngradeOps/Any with .ops)
            nested = getattr(op, "ops", None)
            if isinstance(nested, list):
                _filter_ops(nested)
                kept.append(op)
                continue

            # Фильтрация DropTableOp над таблицей версий
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
    Это защищает от случайной потери данных в автогенерации.
    """
    if ALLOW_DROPS:
        return
    if not getattr(script, "upgrade_ops", None):
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

            op_type = type(op).__name__
            to_remove = (
                (DropTableOp and isinstance(op, DropTableOp))
                or (DropColumnOp and isinstance(op, DropColumnOp))
                or (DropConstraintOp and isinstance(op, DropConstraintOp))
                or (DropIndexOp and isinstance(op, DropIndexOp))
            )

            if to_remove:
                removed.append(op_type)
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

    # 1) Не создаём пустые миграции
    if getattr(script, "upgrade_ops", None) and script.upgrade_ops.is_empty():
        _debug("Autogenerate не нашёл изменений — пустая миграция отменена.")
        directives[:] = []
        return

    # 2) Фильтрация ошибочных операций над таблицей версий
    _strip_accidental_version_table_ops(script)

    # 3) Защитный режим — удаляем DROP-операции
    _remove_destructive_ops_if_not_allowed(script)


# ======================================================================
# 6) Конфигурация Alembic context
# ======================================================================


def _schema_translate_map() -> Optional[dict[str, str]]:
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


def _configure_context(connection: Optional[Connection] = None) -> None:
    """
    Общая настройка Alembic context для online/offline.
    """
    url = get_url_from_env_or_cfg()
    render_as_batch = _is_sqlite_url(url)

    # Если это PostgreSQL и схема для таблицы версий не указана,
    # по умолчанию фиксируем 'public' (чтобы не было сюрпризов).
    version_table_schema = ALEMBIC_VERSION_TABLE_SCHEMA
    if version_table_schema is None and _is_postgres_url(url):
        version_table_schema = "public"

    include_schemas_cfg = config.get_main_option("include_schemas", "true").lower() in (
        "1",
        "true",
        "yes",
    )

    opts: dict[str, Any] = dict(
        target_metadata=target_metadata,
        include_object=include_object,
        include_name=include_name,
        process_revision_directives=process_revision_directives,
        compare_type=True,
        compare_server_default=True,
        render_as_batch=render_as_batch,
        literal_binds=True if context.is_offline_mode() else False,
        dialect_opts={"paramstyle": "named"},
        # Надёжная фиксация таблицы версий
        version_table=ALEMBIC_VERSION_TABLE,
        version_table_schema=version_table_schema,
        # Схемы включаем — это важно для сложных проектов
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
    # Не перетираем URL из INI, а берём его через нашу функцию с приоритетом INI > ENV
    sqlalchemy_section = dict(config.get_section(config.config_ini_section) or {})
    sqlalchemy_section["sqlalchemy.url"] = get_url_from_env_or_cfg()

    connectable = engine_from_config(
        sqlalchemy_section,
        prefix="sqlalchemy.",
        poolclass=None,  # на миграциях ок
        future=True,
    )

    try:
        with connectable.connect() as connection:
            # SQLite: включаем foreign_keys (на всякий случай)
            try:
                if _is_sqlite_url(sqlalchemy_section["sqlalchemy.url"]):
                    connection.execute(text("PRAGMA foreign_keys=ON"))
            except Exception:
                pass

            # Быстрая проверка соединения (не критично при ошибке)
            try:
                connection.execute(text("SELECT 1"))
            except (OperationalError, ProgrammingError):
                _debug("Подключение есть, но первичная проверка не прошла.")

            # Диагностика: куда подключились (без фатала при ошибке)
            try:
                if _is_postgres_url(sqlalchemy_section["sqlalchemy.url"]):
                    info = connection.execute(
                        text("select current_database(), current_user, current_schema()")
                    ).fetchone()
                    sp = connection.execute(text("show search_path")).scalar_one()
                    print(
                        f"[alembic] DB={info[0]} USER={info[1]} SCHEMA={info[2]} SEARCH_PATH={sp}"
                    )
                elif _is_sqlite_url(sqlalchemy_section["sqlalchemy.url"]):
                    dbfile = sqlalchemy_section["sqlalchemy.url"].replace("sqlite:///", "")
                    print(f"[alembic] SQLite DB file: {dbfile}")
            except Exception as e:
                print(f"[alembic] warn: cannot fetch connection info: {e}")

            _configure_context(connection=connection)

            # Все миграции — в одной транзакции (PostgreSQL: транзакционный DDL)
            with context.begin_transaction():
                context.run_migrations()
    finally:
        connectable.dispose()


# ======================================================================
# 8) Точка входа
# ======================================================================

if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
