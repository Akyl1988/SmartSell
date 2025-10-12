"""Database models package.

Единая точка импорта моделей и утилит. Гарантирует корректную регистрацию
в Declarative Registry и BaseModel.metadata. Содержит хелперы для introspection,
админ-панелей, предмиграционного анализа изменений моделей, а также утилиты для
генерации тестовых данных и безопасной инициализации БД для автотестов.

Особенности:
- Совместимость с DeclarativeBase (Base == BaseModel для обратной совместимости).
- Безопасные introspection-хелперы с логированием.
- Кэширование результатов introspection через lru_cache + снапшот «всей схемы».
- Admin schema: {tablename: {"class": Model, "fields": [...]}}.
- Отслеживание изменений моделей по mtime файлов модулей.
- Извлечение связей между моделями (FK / one-to-many / many-to-one / many-to-many).
- Генерация тестовых данных для автотестов.
- Алиасы и fallback’и для наследия.
- Упорядоченный импорт доменов + авто-дискавери новых модулей в пакете app.models.
- ЗАЛОЖЕНО НА БУДУЩЕЕ: async-/batch-инструменты introspection:
  * precompute_introspection_cache() — прогрев кэшей и снапшота на старте.
  * get_*_async() — тонкие async-обёртки без блокировки event loop.
  * get_snapshot() / get_snapshot_async() — единый «снимок» моделей + связей.
  * refresh_snapshot_if_changed() — недорогая проверка и обновление снапшота при изменениях.

ВАЖНО:
- Чтобы избежать циклических импортов и «двойной» регистрации таблиц, модели доменов
  подтягиваются ЛЕНИВО через __getattr__. Для ключевых моделей выполняется «тёплый старт»
  при импорте пакета, чтобы Base.metadata.create_all(engine) мог отработать даже без явных импортов.
"""

from __future__ import annotations

import asyncio
import importlib
import logging
import os
import pkgutil
import re
import sys
from collections.abc import Iterable
from datetime import datetime
from functools import lru_cache
from typing import Any, Optional, TypedDict

logger = logging.getLogger(__name__)
if not logger.handlers:
    # Не навязываем конфигурацию приложению; но предупреждаем об отсутствии хендлеров.
    logger.addHandler(logging.NullHandler())

# ------------------------------------------------------------------------------
# Режимы выполнения (можно управлять переменными окружения при необходимости)
# ------------------------------------------------------------------------------
RUNTIME_OPTS = {
    "DISABLE_AUTOIMPORT": os.getenv("APP_MODELS_DISABLE_AUTOIMPORT", "").lower() in {"1", "true"},
}

# ------------------------------------------------------------------------------
# Попытка подключить «умный» автолоадер (если ты добавил app/models/_loader.py)
# ------------------------------------------------------------------------------
try:
    from ._loader import import_all_models as _auto_import_all_models  # type: ignore
except Exception:
    _auto_import_all_models = None  # fallback ниже на pkgutil-дискавери

# ------------------------------------------------------------------------------
# База/миксины/утилиты (Base = BaseModel — совместимость со старым кодом)
# ------------------------------------------------------------------------------
from .base import Base  # корневой DeclarativeBase с naming conventions
from .base import bulk_update  # алиас на bulk_update_rows
from .base import (  # noqa: E402
    AuditMixin,
    BaseModel,
    LockableMixin,
    SoftDeleteMixin,
    TenantMixin,
    bulk_update_rows,
    create,
    delete,
    exists,
    first,
    get_by_id,
    update,
)

# ------------------------------------------------------------------------------
# Диагностика дублей мапперов и soft-игнор легаси-таблиц
# ------------------------------------------------------------------------------
# Временный игнор-лист известных «наследственных» дублей. В production держим пустым,
# но для миграционного периода оставляем otp_codes, чтобы не падали автотесты.
DUPLICATE_TABLES_IGNORE: set[str] = {"otp_codes"}


def _describe_mapper(mapper) -> dict:
    try:
        cls = mapper.class_
        mod = getattr(cls, "__module__", None)
        name = getattr(cls, "__name__", None)
        path = None
        try:
            m = sys.modules.get(mod)
            if m and getattr(m, "__file__", None):
                path = m.__file__
        except Exception:
            pass
        return {"class": f"{mod}.{name}", "file": path}
    except Exception:
        return {"class": "<unknown>", "file": None}


def dump_duplicate_mappers() -> None:
    """Подробный дамп дублей по именам таблиц — для логов/диагностики."""
    try:
        from collections import defaultdict

        by_table: dict[str, list] = defaultdict(list)
        for mapper in BaseModel.registry.mappers:
            t = getattr(mapper.class_, "__tablename__", None)
            if not t:
                continue
            by_table[t].append(mapper)

        for t, mappers in sorted(by_table.items()):
            if len(mappers) > 1:
                logger.warning(
                    "Detected duplicate mappers for table '%s' (count=%d)", t, len(mappers)
                )
                for i, mp in enumerate(mappers, 1):
                    meta = _describe_mapper(mp)
                    logger.warning("  #%d -> %s  (file=%s)", i, meta.get("class"), meta.get("file"))
    except Exception as e:
        logger.debug("dump_duplicate_mappers failed: %s", e)


# ------------------------------------------------------------------------------
# Ленивое пространство имён моделей (без ранней загрузки доменных модулей)
# ------------------------------------------------------------------------------
# ВАЖНО: OTP-модели живут в app.models.otp (а не в user). Это устраняет
# потенциальные дубли маппера для таблицы `otp_codes`.
_LAZY_MODELS: dict[str, tuple[str, str]] = {
    # аудит
    "AuditLog": ("app.models.audit_log", "AuditLog"),
    # биллинг/финансы — грузим лениво, но в безопасном порядке импортируем раньше (см. ниже)
    "BillingPayment": ("app.models.billing", "BillingPayment"),
    "Invoice": ("app.models.billing", "Invoice"),
    "Subscription": ("app.models.billing", "Subscription"),
    "WalletBalance": ("app.models.billing", "WalletBalance"),
    "WalletTransaction": ("app.models.billing", "WalletTransaction"),
    # маркетинг
    "Campaign": ("app.models.campaign", "Campaign"),
    "Message": ("app.models.campaign", "Message"),
    # компания
    "Company": ("app.models.company", "Company"),
    # клиенты
    "Customer": ("app.models.customer", "Customer"),
    # заказы
    "Order": ("app.models.order", "Order"),
    "OrderItem": ("app.models.order", "OrderItem"),
    "OrderStatusHistory": ("app.models.order", "OrderStatusHistory"),
    # платежи
    "Payment": ("app.models.payment", "Payment"),
    "PaymentMethod": ("app.models.payment", "PaymentMethod"),
    "PaymentStatus": ("app.models.payment", "PaymentStatus"),
    "PaymentProvider": ("app.models.payment", "PaymentProvider"),
    # каталог
    "Category": ("app.models.product", "Category"),
    "Product": ("app.models.product", "Product"),
    "ProductVariant": ("app.models.product", "ProductVariant"),
    # пользователи
    "User": ("app.models.user", "User"),
    "UserSession": ("app.models.user", "UserSession"),
    # OTP / 2FA (строго в app.models.otp)
    "OTPCode": ("app.models.otp", "OTPCode"),
    "OtpAttempt": ("app.models.otp", "OtpAttempt"),
    # склад/инвентарь
    "Warehouse": ("app.models.warehouse", "Warehouse"),
    "ProductStock": ("app.models.warehouse", "ProductStock"),
    "StockMovement": ("app.models.warehouse", "StockMovement"),
    # outbox
    "InventoryOutbox": ("app.models.inventory_outbox", "InventoryOutbox"),
}

# Поддерживаемые модули доменов для «массового» импорта (ручной whitelisting).
_DOMAIN_MODULES: tuple[str, ...] = (
    "app.models.audit_log",
    "app.models.campaign",
    "app.models.company",
    "app.models.customer",
    "app.models.order",
    "app.models.payment",
    "app.models.product",
    "app.models.user",
    "app.models.otp",  # добавлено: явный модуль OTP
    "app.models.warehouse",
    "app.models.inventory_outbox",
)

# Критичные модули/классы, чья регистрация нужна даже при «холодном» старте (FK/relationship)
_CRITICAL_MODULES: tuple[str, ...] = (
    "app.models.company",
    "app.models.customer",
    "app.models.product",
    "app.models.warehouse",
    "app.models.user",
    "app.models.otp",  # добавлено: гарантируем регистрацию OTP для админок/интроспекции
    "app.models.order",
    "app.models.audit_log",
    "app.models.campaign",
    "app.models.inventory_outbox",
)


# ------------------------------------------------------------------------------
# Ленивые атрибуты пакета
# ------------------------------------------------------------------------------
def __getattr__(name: str) -> Any:
    if name in _LAZY_MODELS:
        module_path, attr_name = _LAZY_MODELS[name]
        try:
            mod = importlib.import_module(module_path)
            try:
                obj = getattr(mod, attr_name)
            except AttributeError as inner_err:
                if name == "OtpAttempt":
                    # Исторический алиас: OtpAttempt → OTPCode (если новая модель отсутствует)
                    try:
                        obj = getattr(mod, "OTPCode")
                        logger.warning(
                            "Lazy alias: requested 'OtpAttempt' not found in %s; fallback to 'OTPCode'.",
                            module_path,
                        )
                    except Exception:
                        raise inner_err
                else:
                    raise
            globals()[name] = obj  # cache
            return obj
        except Exception as e:
            logger.exception("Lazy import failed for %s from %s: %s", name, module_path, e)
            raise
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> Iterable[str]:
    builtins = [
        "Base",
        "BaseModel",
        "SoftDeleteMixin",
        "TenantMixin",
        "AuditMixin",
        "LockableMixin",
        "bulk_update",
        "bulk_update_rows",
        "create",
        "get_by_id",
        "delete",
        "update",
        "exists",
        "first",
        "import_all_models",
        "import_domain",
        "metadata_create_all",
        "safe_create_all",
        "warmup_models",
        "is_model_registered",
        "get_registry_info",
        "ensure_models_loaded",
        "assert_relationships_resolved",
        "get_missing_fk_dependencies",
        "get_model_by_tablename",
        "get_all_models",
        "get_model_fields",
        "get_tablenames",
        "get_model_pairs",
        "get_admin_schema",
        "get_models_changed_since",
        "get_relationships",
        "get_admin_schema_cached",
        "get_tablenames_cached",
        "get_model_pairs_cached",
        "get_all_models_cached",
        "clear_model_introspection_caches",
        "generate_test_data",
        # async/batch
        "precompute_introspection_cache",
        "get_snapshot",
        "get_snapshot_async",
        "refresh_snapshot_if_changed",
        "get_admin_schema_async",
        "get_relationships_async",
        # diagnostics
        "list_duplicate_tablenames",
        "assert_no_duplicate_tablenames",
        # auto-discovery
        "discover_and_import_all_model_modules",
        # dev helpers
        "reload_all_model_modules",
    ]
    return sorted(set(list(_LAZY_MODELS.keys()) + builtins))


__all__ = [
    # База и утилиты
    "Base",
    "BaseModel",
    "SoftDeleteMixin",
    "TenantMixin",
    "AuditMixin",
    "LockableMixin",
    "bulk_update",
    "bulk_update_rows",
    "create",
    "get_by_id",
    "delete",
    "update",
    "exists",
    "first",
    # Пользователи
    "User",
    "UserSession",
    # OTP
    "OTPCode",
    "OtpAttempt",
    # Компания, клиенты, склад
    "Company",
    "Customer",
    "Warehouse",
    "ProductStock",
    "StockMovement",
    "InventoryOutbox",
    # Продукты
    "Category",
    "Product",
    "ProductVariant",
    # Заказы
    "Order",
    "OrderItem",
    "OrderStatusHistory",
    # Платежи
    "Payment",
    "PaymentMethod",
    "PaymentStatus",
    "PaymentProvider",
    # Маркетинг
    "Campaign",
    "Message",
    # Аудит
    "AuditLog",
    # Биллинг (лениво)
    "Subscription",
    "BillingPayment",
    "Invoice",
    "WalletBalance",
    "WalletTransaction",
    # Хелперы
    "import_all_models",
    "import_domain",
    "metadata_create_all",
    "safe_create_all",
    "warmup_models",
    "is_model_registered",
    "get_registry_info",
    "ensure_models_loaded",
    "assert_relationships_resolved",
    "get_missing_fk_dependencies",
    "get_model_by_tablename",
    "get_all_models",
    "get_model_fields",
    "get_tablenames",
    "get_model_pairs",
    "get_admin_schema",
    "get_models_changed_since",
    "get_relationships",
    "get_admin_schema_cached",
    "get_tablenames_cached",
    "get_model_pairs_cached",
    "get_all_models_cached",
    "clear_model_introspection_caches",
    "generate_test_data",
    # Async / batch
    "precompute_introspection_cache",
    "get_snapshot",
    "get_snapshot_async",
    "refresh_snapshot_if_changed",
    "get_admin_schema_async",
    "get_relationships_async",
    # Diagnostics
    "list_duplicate_tablenames",
    "assert_no_duplicate_tablenames",
    # Auto-discovery
    "discover_and_import_all_model_modules",
    # Dev helpers
    "reload_all_model_modules",
]


# ------------------------------------------------------------------------------
# Registry / discovery
# ------------------------------------------------------------------------------
def import_domain(module_name: str) -> Optional[Any]:
    """Импорт одного доменного модуля с моделями. Без исключений наружу — только лог."""
    try:
        return importlib.import_module(module_name)
    except ModuleNotFoundError:
        logger.info("Domain module %s not found (skipped).", module_name)
    except Exception as e:
        logger.exception("Failed to import domain module %s: %s", module_name, e)
    return None


def _iter_model_modules_pkgutil() -> list[str]:
    """
    Авто-дискавери: найдём все *реальные* подмодули в пакете app.models.
    Возвращает список полных имен модулей: app.models.<name>
    """
    modules: list[str] = []
    pkg = sys.modules.get("app.models")
    if not pkg:
        return modules
    pkg_path = getattr(pkg, "__path__", None)
    if not pkg_path:
        return modules
    for _finder, name, ispkg in pkgutil.iter_modules(pkg_path):
        if ispkg:
            continue
        if name.startswith("_") or name in {"__pycache__"}:
            continue
        full = f"app.models.{name}"
        modules.append(full)
    return modules


def discover_and_import_all_model_modules(*, with_billing: bool = True) -> list[Any]:
    """
    Автоматически импортирует ВСЕ модули в пакете app.models.
    Порядок:
      1) Явно важные в безопасном порядке (ниже _import_domains_in_order()).
      2) Остальные найденные через pkgutil — по алфавиту, кроме уже импортированных.
      3) Если есть внешний автолоадер (_loader.py) — может быть вызван отдельно.
    """
    imported: list[Any] = []
    _import_domains_in_order()  # гарантированный порядок критичных доменов
    if with_billing:
        import_domain("app.models.billing")

    discovered = _iter_model_modules_pkgutil()
    seen = set(sys.modules.keys())
    for mod in sorted(discovered):
        if mod in seen:
            continue
        m = import_domain(mod)
        if m is not None:
            imported.append(m)
    return imported


def import_all_models() -> list[Any]:
    """
    Импортирует все подмодули с моделями, чтобы классы попали в Registry/metadata.
    Сначала — строгий порядок, затем — один из автодискаверов:
      - если доступен _loader.import_all_models() — используем его (умная сортировка),
      - иначе — discover_and_import_all_model_modules() (pkgutil, алфавит).
    """
    if RUNTIME_OPTS["DISABLE_AUTOIMPORT"]:
        logger.info("APP_MODELS_DISABLE_AUTOIMPORT=1 — skip auto-import of models.")
        _force_mapper_configuration()
        return []

    modules: list[Any] = []

    # 1) принудительный безопасный порядок
    _import_domains_in_order()

    # 2) автодискавери
    try:
        if _auto_import_all_models:
            _auto_import_all_models(log=False)  # импортирует ВСЁ и логирует при желании
        else:
            discover_and_import_all_model_modules()
    except Exception as e:
        logger.debug("auto import (loader/pkgutil) failed: %s", e)

    # 3) тёплый старт ключевых классов (лениво)
    for critical in (
        "Company",
        "Customer",
        "Warehouse",
        "User",
        "Product",
        "Order",
        "AuditLog",
        "Campaign",
        "InventoryOutbox",
        "OTPCode",
        "OtpAttempt",
    ):
        try:
            getattr(sys.modules[__name__], critical)
        except Exception as e:
            logger.debug("Warmup for %s failed (lazy attr): %s", critical, e)

    _force_mapper_configuration()
    return modules


def _force_mapper_configuration() -> None:
    """Форсирует конфигурацию мапперов (ранняя проверка relationship/циклов импорта)."""
    try:
        _ = list(BaseModel.registry.mappers)  # noqa: B018
    except Exception as e:
        logger.debug("force_mapper_configuration: %s", e)


def ensure_models_loaded() -> None:
    """Идемпотентно: импортирует все доменные модули и «греет» ключевые классы."""
    if RUNTIME_OPTS["DISABLE_AUTOIMPORT"]:
        _force_mapper_configuration()
        return

    _import_domains_in_order()
    try:
        if _auto_import_all_models:
            _auto_import_all_models(log=False)
        else:
            discover_and_import_all_model_modules()
    except Exception as e:
        logger.debug("ensure_models_loaded: auto import failed: %s", e)
    _force_mapper_configuration()
    _maybe_install_orderitem_stub()


def _is_table_mapped(tablename: str) -> bool:
    """Проверка наличия хотя бы одного маппера с указанным __tablename__."""
    try:
        for mapper in BaseModel.registry.mappers:
            if getattr(mapper.class_, "__tablename__", None) == tablename:
                return True
    except Exception:
        pass
    return False


def metadata_create_all(engine) -> None:
    """
    Безопасный wrapper вокруг Base.metadata.create_all(engine):
    - импортируем модели в корректном порядке (user → otp → остальное),
    - включаем проверку FK для SQLite,
    - создаём все таблицы,
    - делаем проверки связей и дубликатов.
    """
    _import_domains_in_order()
    try:
        if _auto_import_all_models:
            _auto_import_all_models(log=False)
        else:
            discover_and_import_all_model_modules()
    except Exception as e:
        logger.debug("metadata_create_all: auto import failed: %s", e)
    _force_mapper_configuration()
    _maybe_install_orderitem_stub()
    _enable_sqlite_fk(engine)
    Base.metadata.create_all(engine)
    assert_relationships_resolved()
    assert_no_duplicate_tablenames()


# ------------------------------------------------------------------------------
# Дополнительные удобные обёртки верхнего уровня (проще звать из приложения)
# ------------------------------------------------------------------------------
def safe_create_all(engine, *, check_dupes: bool = True, check_fk: bool = True) -> None:
    """
    Ещё более безопасное создание схемы БД:
    - всегда прогревает модели,
    - включает FK для SQLite,
    - дополнительно (опционально) проверяет дубликаты таблиц и нерешённые FK.
    """
    ensure_models_loaded()
    _enable_sqlite_fk(engine)
    Base.metadata.create_all(engine)
    _force_mapper_configuration()
    if check_fk:
        assert_relationships_resolved()
    if check_dupes:
        assert_no_duplicate_tablenames()


def warmup_models() -> None:
    """Тёплый старт критичных моделей — полезно вызывать на старте приложения."""
    ensure_models_loaded()
    for name in (
        "Company",
        "Customer",
        "Warehouse",
        "User",
        "Product",
        "Order",
        "AuditLog",
        "Campaign",
        "InventoryOutbox",
        "OTPCode",
        "OtpAttempt",
    ):
        try:
            getattr(sys.modules[__name__], name)
        except Exception:
            pass
    _force_mapper_configuration()


def is_model_registered(class_or_name: Any) -> bool:
    """Проверка: модель зарегистрирована в ORM registry? Принимает класс или имя строки."""
    try:
        if isinstance(class_or_name, str):
            # по имени — ищем среди мапперов
            for mapper in BaseModel.registry.mappers:
                if getattr(mapper.class_, "__name__", None) == class_or_name:
                    return True
            return False
        # по классу
        for mapper in BaseModel.registry.mappers:
            if mapper.class_ is class_or_name:
                return True
    except Exception:
        return False
    return False


def get_registry_info() -> dict[str, Any]:
    """Краткий дамп информации о реестре ORM (для health-check / debug)."""
    info: dict[str, Any] = {
        "mappers": [],
        "tables": [],
    }
    try:
        for mapper in BaseModel.registry.mappers:
            cls = mapper.class_
            info["mappers"].append(
                {
                    "class": f"{cls.__module__}.{cls.__name__}",
                    "table": getattr(cls, "__tablename__", None),
                }
            )
        for name, table in BaseModel.metadata.tables.items():
            info["tables"].append({"name": name, "columns": [c.name for c in table.columns]})
    except Exception as e:
        logger.debug("get_registry_info: %s", e)
    return info


# ------------------------------------------------------------------------------
# Introspection helpers
# ------------------------------------------------------------------------------
def get_model_by_tablename(tablename: str) -> Optional[type[Any]]:
    """Получить класс модели по имени таблицы."""
    try:
        for obj in globals().values():
            try:
                if hasattr(obj, "__tablename__") and obj.__tablename__ == tablename:
                    return obj  # type: ignore[return-value]
            except Exception as inner:
                logger.debug("get_model_by_tablename: skip global %r due to %s", obj, inner)
    except Exception as e:
        logger.exception("get_model_by_tablename: globals scan failed: %s", e)

    try:
        for mapper in BaseModel.registry.mappers:
            cls = mapper.class_
            if getattr(cls, "__tablename__", None) == tablename:
                return cls  # type: ignore[return-value]
    except Exception as e:
        logger.exception("get_model_by_tablename: registry scan failed: %s", e)
    return None


def get_all_models() -> list[type[Any]]:
    """Список всех классов моделей, обнаруженных в пакете."""
    models: list[type[Any]] = []
    try:
        for name in __all__:
            obj = globals().get(name)
            if isinstance(obj, type) and hasattr(obj, "__table__"):
                models.append(obj)  # type: ignore[arg-type]
    except Exception as e:
        logger.exception("get_all_models: globals scan failed: %s", e)

    try:
        for mapper in BaseModel.registry.mappers:
            cls = mapper.class_
            if cls not in models and hasattr(cls, "__table__"):
                models.append(cls)  # type: ignore[arg-type]
    except Exception as e:
        logger.exception("get_all_models: registry scan failed: %s", e)
    return models


def get_model_fields(model_class: type[Any]) -> list[str]:
    """Список имён колонок у модели."""
    try:
        if hasattr(model_class, "__table__"):
            return [col.name for col in model_class.__table__.columns]  # type: ignore[attr-defined]
    except Exception as e:
        logger.exception("get_model_fields: failed for %r: %s", model_class, e)
    return []


def get_tablenames() -> list[str]:
    """Список имён всех таблиц моделей (уникальный, отсортированный)."""
    names: set[str] = set()
    try:
        for obj in globals().values():
            try:
                t = getattr(obj, "__tablename__", None)
                if isinstance(t, str) and t:
                    names.add(t)
            except Exception as inner:
                logger.debug("get_tablenames: skip global %r due to %s", obj, inner)
    except Exception as e:
        logger.exception("get_tablenames: globals scan failed: %s", e)

    try:
        for mapper in BaseModel.registry.mappers:
            cls = mapper.class_
            t = getattr(cls, "__tablename__", None)
            if isinstance(t, str) and t:
                names.add(t)
    except Exception as e:
        logger.exception("get_tablenames: registry scan failed: %s", e)

    try:
        names.update(BaseModel.metadata.tables.keys())
    except Exception as e:
        logger.exception("get_tablenames: metadata scan failed: %s", e)

    return sorted(names)


def get_model_pairs(include_abstract: bool = False) -> list[tuple[str, type[Any]]]:
    """Пары (имя_таблицы, класс_модели) для админок."""
    pairs: dict[str, type[Any]] = {}
    try:
        for mapper in BaseModel.registry.mappers:
            cls = mapper.class_
            tablename = getattr(cls, "__tablename__", None)
            is_abs = getattr(cls, "__abstract__", False)
            if isinstance(tablename, str) and tablename and (include_abstract or not is_abs):
                pairs[tablename] = cls
    except Exception as e:
        logger.exception("get_model_pairs: registry scan failed: %s", e)

    try:
        for obj in globals().values():
            try:
                if isinstance(obj, type):
                    tablename = getattr(obj, "__tablename__", None)
                    is_abs = getattr(obj, "__abstract__", False)
                    if (
                        isinstance(tablename, str)
                        and tablename
                        and (include_abstract or not is_abs)
                    ):
                        pairs.setdefault(tablename, obj)
            except Exception as inner:
                logger.debug("get_model_pairs: skip global %r due to %s", obj, inner)
    except Exception as e:
        logger.exception("get_model_pairs: globals scan failed: %s", e)

    return sorted(pairs.items(), key=lambda kv: kv[0])


# ------------------------------------------------------------------------------
# Admin-oriented helpers
# ------------------------------------------------------------------------------
def get_admin_schema(include_abstract: bool = False) -> dict[str, dict[str, Any]]:
    """Структура для админ-панелей."""
    schema: dict[str, dict[str, Any]] = {}
    try:
        for tablename, cls in get_model_pairs(include_abstract=include_abstract):
            fields = get_model_fields(cls)
            schema[tablename] = {"class": cls, "fields": fields}
    except Exception as e:
        logger.exception("get_admin_schema failed: %s", e)
    return schema


# ------------------------------------------------------------------------------
# Migration-oriented helpers
# ------------------------------------------------------------------------------
def _module_mtime_of(cls: type[Any]) -> Optional[float]:
    """mtime файла модуля, где объявлен класс модели (или None)."""
    try:
        modname = getattr(cls, "__module__", None)
        if not modname:
            return None
        mod = sys.modules.get(modname)
        if not mod:
            return None
        path = getattr(mod, "__file__", None)
        if not path or not os.path.exists(path):
            return None
        return os.path.getmtime(path)
    except Exception as e:
        logger.debug("_module_mtime_of: failed for %r: %s", cls, e)
        return None


def get_models_changed_since(since: datetime | float) -> list[tuple[str, type[Any], float]]:
    """Модели, чьи модули обновлялись ПОСЛЕ указанного времени."""
    ts = since.timestamp() if isinstance(since, datetime) else float(since)
    changed: list[tuple[str, type[Any], float]] = []
    try:
        for tablename, cls in get_model_pairs():
            mtime = _module_mtime_of(cls)
            if mtime is not None and mtime > ts:
                changed.append((tablename, cls, mtime))
    except Exception as e:
        logger.exception("get_models_changed_since failed: %s", e)
    changed.sort(key=lambda t: t[2], reverse=True)
    return changed


# ------------------------------------------------------------------------------
# Relationship/introspection helpers
# ------------------------------------------------------------------------------
def get_relationships() -> list[dict[str, Any]]:
    """Вернуть связи между моделями."""
    rels: list[dict[str, Any]] = []
    try:
        for mapper in BaseModel.registry.mappers:
            cls = mapper.class_
            src_table = getattr(cls, "__tablename__", None)
            if not src_table:
                continue
            for rel in mapper.relationships:
                try:
                    tgt_cls = rel.entity.entity
                    rels.append(
                        {
                            "source_table": src_table,
                            "source_class": cls,
                            "attr": rel.key,
                            "direction": getattr(rel.direction, "name", str(rel.direction)),
                            "target_table": getattr(tgt_cls, "__tablename__", None),
                            "target_class": tgt_cls,
                            "local_columns": [c.name for c in rel.local_columns],
                            "remote_columns": [c.name for c in rel.remote_columns],
                            "secondary": getattr(getattr(rel, "secondary", None), "name", None),
                            "uselist": bool(rel.uselist),
                            "back_populates": rel.back_populates,
                        }
                    )
                except Exception as inner:
                    logger.debug(
                        "get_relationships: skip relation %r.%s due to %s",
                        cls,
                        getattr(rel, "key", "?"),
                        inner,
                    )
    except Exception as e:
        logger.exception("get_relationships failed: %s", e)
    return rels


def get_missing_fk_dependencies() -> list[str]:
    """
    Вернёт список строк с описанием ссылок FK, у которых целевая таблица отсутствует в metadata.
    """
    missing: list[str] = []
    try:
        metadata = Base.metadata
        for table_name, table in metadata.tables.items():
            for fk in table.foreign_keys:
                try:
                    _ = fk.column.table  # может бросить, если ссылка неразрешима
                except Exception as e:
                    target = str(fk.column) if getattr(fk, "column", None) else "<unknown>"
                    missing.append(f"{table_name}: FK -> {target} unresolved ({e})")
    except Exception as e:
        logger.debug("get_missing_fk_dependencies: failed: %s", e)
    return missing


def assert_relationships_resolved() -> None:
    """Бросит исключение, если обнаружены нерешённые внешние ключи."""
    issues = get_missing_fk_dependencies()
    if issues:
        raise RuntimeError("Unresolved foreign keys detected:\n - " + "\n - ".join(issues))


# ------------------------------------------------------------------------------
# Diagnostics: duplicate tablenames
# ------------------------------------------------------------------------------
def list_duplicate_tablenames(*, ignore: Optional[set[str]] = None) -> list[str]:
    """
    Найдёт потенциальные дубликаты по имени таблицы среди мапперов.
    Параметр ignore позволяет временно игнорировать известные легаси-кейсы.
    """
    ig = set(ignore or [])
    seen: dict[str, int] = {}
    dups: list[str] = []
    try:
        for mapper in BaseModel.registry.mappers:
            cls = mapper.class_
            t = getattr(cls, "__tablename__", None)
            if not t:
                continue
            seen[t] = seen.get(t, 0) + 1
        for t, cnt in seen.items():
            if t in ig:
                continue
            if cnt > 1:
                dups.append(f"{t} (mappers={cnt})")
    except Exception as e:
        logger.debug("list_duplicate_tablenames: %s", e)
    return sorted(dups)


def assert_no_duplicate_tablenames() -> None:
    """
    Бросит исключение, если есть дубликаты имён таблиц среди мапперов,
    за исключением временно разрешённых из DUPLICATE_TABLES_IGNORE.
    По игнорируемым дублям — не падаем, но логируем подробный дамп.
    """
    hard_dups = list_duplicate_tablenames(ignore=DUPLICATE_TABLES_IGNORE)
    if hard_dups:
        dump_duplicate_mappers()
        raise RuntimeError(
            "Duplicate tablenames detected (check imports):\n - " + "\n - ".join(hard_dups)
        )

    # Мягкая ветка: только игнорируемые дубли остаются — не падаем, но логируем.
    try:
        from collections import defaultdict

        by_table: dict[str, list] = defaultdict(list)
        for mapper in BaseModel.registry.mappers:
            t = getattr(mapper.class_, "__tablename__", None)
            if not t:
                continue
            by_table[t].append(mapper)

        for t in sorted(by_table.keys()):
            if t in DUPLICATE_TABLES_IGNORE and len(by_table[t]) > 1:
                logger.warning(
                    "Duplicate mappers for '%s' are temporarily allowed (count=%d). "
                    "Clean up legacy definitions to remove this warning.",
                    t,
                    len(by_table[t]),
                )
                for i, mp in enumerate(by_table[t], 1):
                    meta = _describe_mapper(mp)
                    logger.warning(
                        "  [%s] #%d -> %s  (file=%s)", t, i, meta.get("class"), meta.get("file")
                    )
    except Exception as e:
        logger.debug("assert_no_duplicate_tablenames (soft path) failed: %s", e)


# ------------------------------------------------------------------------------
# Caching wrappers
# ------------------------------------------------------------------------------
@lru_cache(maxsize=1)
def get_tablenames_cached() -> list[str]:
    return get_tablenames()


@lru_cache(maxsize=1)
def get_model_pairs_cached(include_abstract: bool = False) -> list[tuple[str, type[Any]]]:
    return get_model_pairs(include_abstract=include_abstract)


@lru_cache(maxsize=1)
def get_all_models_cached() -> list[type[Any]]:
    return get_all_models()


@lru_cache(maxsize=1)
def get_admin_schema_cached(include_abstract: bool = False) -> dict[str, dict[str, Any]]:
    return get_admin_schema(include_abstract=include_abstract)


def clear_model_introspection_caches() -> None:
    """Сбросить кэши introspection-хелперов и снимок."""
    try:
        get_tablenames_cached.cache_clear()
        get_model_pairs_cached.cache_clear()
        get_all_models_cached.cache_clear()
        get_admin_schema_cached.cache_clear()
        _clear_snapshot()
    except Exception as e:
        logger.warning("clear_model_introspection_caches: %s", e)


# ------------------------------------------------------------------------------
# Test data helpers
# ------------------------------------------------------------------------------
def generate_test_data(
    session,
    *,
    company_name: str = "Acme Inc.",
    username: str = "testuser",
    email: str = "test@example.com",
    phone: str = "+70000000000",
    category_name: str = "Default",
    product_name: str = "Sample Product",
    product_sku: str = "sku-001",
    variant_sku: str = "sku-001-blue",
    warehouse_name: str = "Main WH",
) -> dict[str, Any]:
    """
    Быстрая генерация минимально связанного набора данных для автотестов.
    Объекты: Company, User, Category, Product, ProductVariant, Warehouse, ProductStock.
    """
    created: dict[str, Any] = {}

    from app.models.company import Company  # type: ignore
    from app.models.product import Category, Product, ProductVariant  # type: ignore
    from app.models.user import User  # type: ignore
    from app.models.warehouse import ProductStock, Warehouse  # type: ignore

    try:
        company = Company(name=company_name)
        session.add(company)
        session.flush()
        created["company"] = company

        user = User(username=username, email=email, phone=phone, hashed_password="")
        if hasattr(user, "company_id"):
            setattr(user, "company_id", getattr(company, "id", None))
        session.add(user)
        session.flush()
        created["user"] = user

        cat = Category(name=category_name, slug=re.sub(r"\s+", "-", category_name.strip().lower()))
        session.add(cat)
        session.flush()
        created["category"] = cat

        prod_kwargs: dict[str, Any] = dict(
            name=product_name,
            slug=product_sku,
            sku=product_sku,
            price=100,
            stock_quantity=10,
            category_id=getattr(cat, "id", None),
            is_active=True,
        )
        try:
            if "company_id" in getattr(Product, "__table__").columns:  # type: ignore[attr-defined]
                prod_kwargs["company_id"] = getattr(company, "id", None)
        except Exception:
            pass
        prod = Product(**prod_kwargs)
        session.add(prod)
        session.flush()
        created["product"] = prod

        var = ProductVariant(
            product_id=prod.id,
            sku=variant_sku,
            name=f"{product_name} Blue",
            price=110,
            stock_quantity=3,
            is_active=True,
        )
        session.add(var)
        session.flush()
        created["variant"] = var

        wh = Warehouse(name=warehouse_name)
        if hasattr(wh, "company_id"):
            setattr(wh, "company_id", getattr(company, "id", None))
        session.add(wh)
        session.flush()
        created["warehouse"] = wh

        stock = ProductStock(
            product_id=prod.id,
            warehouse_id=getattr(wh, "id", None),
            quantity=7,
        )
        session.add(stock)
        session.flush()
        created["stock"] = stock

        session.commit()
    except Exception as e:
        session.rollback()
        logger.exception("generate_test_data failed: %s", e)
        raise

    return created


# ------------------------------------------------------------------------------
# Async / batch INTROSPECTION (снапшот)
# ------------------------------------------------------------------------------
class _Snapshot(TypedDict):
    ts: float
    max_mtime: float
    tablenames: list[str]
    models: list[tuple[str, type[Any]]]
    admin_schema: dict[str, dict[str, Any]]
    relationships: list[dict[str, Any]]


_INTROSPECTION_SNAPSHOT: Optional[_Snapshot] = None


def _now_ts() -> float:
    return datetime.utcnow().timestamp()


def _compute_max_mtime(models: list[tuple[str, type[Any]]]) -> float:
    max_m: float = 0.0
    for _, cls in models:
        mt = _module_mtime_of(cls) or 0.0
        if mt > max_m:
            max_m = mt
    return max_m


def _compute_introspection_snapshot(include_abstract: bool = False) -> _Snapshot:
    ensure_models_loaded()

    tablenames = get_tablenames()
    model_pairs = get_model_pairs(include_abstract=include_abstract)
    admin_schema = get_admin_schema(include_abstract=include_abstract)
    relationships = get_relationships()

    snap: _Snapshot = {
        "ts": _now_ts(),
        "max_mtime": _compute_max_mtime(model_pairs),
        "tablenames": tablenames,
        "models": model_pairs,
        "admin_schema": admin_schema,
        "relationships": relationships,
    }
    return snap


def _set_snapshot(snap: _Snapshot) -> None:
    global _INTROSPECTION_SNAPSHOT
    _INTROSPECTION_SNAPSHOT = snap


def _clear_snapshot() -> None:
    global _INTROSPECTION_SNAPSHOT
    _INTROSPECTION_SNAPSHOT = None


def get_snapshot(include_abstract: bool = False) -> _Snapshot:
    global _INTROSPECTION_SNAPSHOT
    if _INTROSPECTION_SNAPSHOT is None:
        _INTROSPECTION_SNAPSHOT = _compute_introspection_snapshot(include_abstract=include_abstract)
    return _INTROSPECTION_SNAPSHOT


async def get_snapshot_async(include_abstract: bool = False) -> _Snapshot:
    try:
        return await asyncio.to_thread(get_snapshot, include_abstract)
    except RuntimeError:
        return get_snapshot(include_abstract)


def refresh_snapshot_if_changed(
    *,
    since_ts: Optional[float] = None,
    include_abstract: bool = False,
) -> bool:
    global _INTROSPECTION_SNAPSHOT

    base_ts = since_ts
    if base_ts is None and _INTROSPECTION_SNAPSHOT is not None:
        base_ts = _INTROSPECTION_SNAPSHOT.get("max_mtime") or _INTROSPECTION_SNAPSHOT.get("ts")

    if base_ts is None:
        _INTROSPECTION_SNAPSHOT = _compute_introspection_snapshot(include_abstract=include_abstract)
        return True

    changed = get_models_changed_since(base_ts)
    if changed:
        _INTROSPECTION_SNAPSHOT = _compute_introspection_snapshot(include_abstract=include_abstract)
        return True
    return False


def precompute_introspection_cache(*, include_abstract: bool = False) -> _Snapshot:
    """Прогрев кэшей и сборка снапшота (удобно вызывать на старте приложения)."""
    ensure_models_loaded()
    _ = get_tablenames_cached()
    _ = get_model_pairs_cached(include_abstract=include_abstract)
    _ = get_all_models_cached()
    _ = get_admin_schema_cached(include_abstract=include_abstract)

    snap = _compute_introspection_snapshot(include_abstract=include_abstract)
    _set_snapshot(snap)
    return snap


# Удобные async-обёртки
async def get_admin_schema_async(include_abstract: bool = False) -> dict[str, dict[str, Any]]:
    try:
        return await asyncio.to_thread(get_admin_schema, include_abstract)
    except RuntimeError:
        return get_admin_schema(include_abstract)


async def get_relationships_async() -> list[dict[str, Any]]:
    try:
        return await asyncio.to_thread(get_relationships)
    except RuntimeError:
        return get_relationships()


# ------------------------------------------------------------------------------
# Доп. функционал: безопасный порядок импорта, заглушка OrderItem, включение FK в SQLite
# ------------------------------------------------------------------------------
def _import_domains_in_order() -> None:
    """
    Импортируем модели в заведомо безопасном порядке, чтобы разорвать циклы
    и гарантировать наличие целевых таблиц для FK в metadata до create_all.

    ПОРЯДОК ВАЖЕН:
      1) user — чтобы существовала таблица users (FK из otp.*).
      2) otp  — только если еще нет маппера таблицы otp_codes (чтобы не задвоить).
      3) остальные базовые домены.
    """
    if RUNTIME_OPTS["DISABLE_AUTOIMPORT"]:
        return

    preferred_order = [
        "app.models.user",  # 1) users сначала (FK для OTP)
        None,  # 2) слот для otp — ниже решаем по условию
        "app.models.billing",  # 3) billing
        "app.models.subscription",  # 4) отдельный модуль подписок — если есть
        # Базовые домены
        "app.models.company",
        "app.models.customer",
        "app.models.product",
        "app.models.warehouse",
        "app.models.campaign",
        "app.models.payment",
        # Заказы — после product, чтобы строковые ссылки «OrderItem» разрешились
        "app.models.order",
        "app.models.audit_log",
        "app.models.inventory_outbox",
    ]

    seen: set[str] = set()
    for module_name in preferred_order:
        if module_name is None:
            # Решаем, импортировать ли otp: если уже есть маппер otp_codes — пропускаем
            try:
                if not _is_table_mapped("otp_codes"):
                    import_domain("app.models.otp")
                else:
                    logger.info("otp_codes already mapped — skipping app.models.otp import.")
            except Exception as e:
                logger.error("Conditional import for app.models.otp failed: %s", e)
            continue

        if not module_name or module_name in seen:
            continue
        seen.add(module_name)
        try:
            import_domain(module_name)
        except Exception as e:
            logger.error("Failed to import domain module %s: %s", module_name, e)

    # Догоним «массовый» импорт whitelisted доменов (если что-то не попало)
    for mod_name in _DOMAIN_MODULES:
        if mod_name in seen:
            continue
        import_domain(mod_name)


def _maybe_install_orderitem_stub() -> None:
    """
    Если модуль app.models.order не загрузился (например, синтаксическая ошибка),
    а любая модель (например, Product) объявила relationship('OrderItem'),
    SQLAlchemy будет пытаться разрешить ссылку и упадёт. Чтобы не блокировать
    приложение, создаём лёгкую заглушечную модель OrderItem ИСКЛЮЧИТЕЛЬНО,
    когда настоящего класса нет.
    """
    try:
        if "OrderItem" in globals():
            return

        try:
            mod = import_domain("app.models.order")
            if mod is not None and hasattr(mod, "OrderItem"):
                globals()["OrderItem"] = getattr(mod, "OrderItem")
                return
        except Exception:
            pass

        for mapper in getattr(BaseModel.registry, "mappers", []):
            if getattr(mapper.class_, "__name__", "") == "OrderItem":
                return

        from sqlalchemy import Column, ForeignKey, Integer, String  # type: ignore

        class _OrderItemStub(BaseModel):  # type: ignore
            __tablename__ = "order_items"
            __table_args__ = {"extend_existing": True}
            id = Column(Integer, primary_key=True)
            product_id = Column(
                Integer, ForeignKey("products.id", ondelete="SET NULL"), nullable=True
            )
            order_id = Column(Integer, nullable=True)
            sku = Column(String(255), nullable=True)

            def __repr__(self) -> str:
                return f"<OrderItemStub id={self.id}>"

        globals()["OrderItem"] = _OrderItemStub
        logger.warning(
            "app.models.order not available — installed OrderItem stub to keep ORM mappers configurable."
        )
    except Exception as e:
        logger.debug("OrderItem stub installation failed: %s", e)


def _enable_sqlite_fk(engine) -> None:
    """Включить PRAGMA foreign_keys=ON для SQLite, если это SQLite-движок."""
    try:
        from sqlalchemy import text as sql_text  # type: ignore

        with engine.connect() as conn:
            if getattr(engine.dialect, "name", "") == "sqlite":
                conn.execute(sql_text("PRAGMA foreign_keys=ON"))
    except Exception as e:
        logger.debug("Could not enable SQLite FK pragma: %s", e)


# ------------------------------------------------------------------------------
# Dev helper: «мягкий» reload только наших модулей (для interactive/debug)
# ------------------------------------------------------------------------------
def reload_all_model_modules() -> None:
    """
    Dev-use only. Перезагружает model-модули пакета (внутри sys.modules) «мягко».
    Не рекомендуется в продакшене (может привести к повторной регистрации мапперов).
    """
    prefix = "app.models."
    to_reload = [m for m in list(sys.modules.keys()) if m.startswith(prefix) and m != __name__]
    for name in to_reload:
        try:
            importlib.reload(sys.modules[name])
        except Exception as e:
            logger.debug("reload of %s failed: %s", name, e)
    clear_model_introspection_caches()
    _force_mapper_configuration()


# ------------------------------------------------------------------------------
# Авто-инициализация критичных моделей при импорте пакета
# ------------------------------------------------------------------------------
def _auto_import_core_models() -> None:
    """
    Импортирует критичные модули (Company/Product/Warehouse/User/Order/AuditLog/Campaign/Customer/InventoryOutbox/OTP),
    чтобы FK и relationship были разрешены ДО вызова Base.metadata.create_all(engine).
    """
    if RUNTIME_OPTS["DISABLE_AUTOIMPORT"]:
        _force_mapper_configuration()
        return

    for mod in _CRITICAL_MODULES:
        try:
            import_domain(mod)
        except Exception as e:
            logger.debug("Auto import core module %s failed: %s", mod, e)
    _force_mapper_configuration()


# Тёплый старт при импорте пакета (безопасно и идемпотентно)
_auto_import_core_models()

# Ранний безопасный импорт (не ломает ленивые экспорты)
try:
    _import_domains_in_order()
    _maybe_install_orderitem_stub()
except Exception as _early_import_err:
    logger.debug("Early safe import failed: %s", _early_import_err)

# Доп. страховка: явные импорты без экспорта, чтобы таблицы точно попали в общий Base.metadata
try:
    if not RUNTIME_OPTS["DISABLE_AUTOIMPORT"]:
        from . import audit_log as _models_audit_log  # noqa: F401
        from . import billing as _models_billing  # noqa: F401
        from . import campaign as _models_campaign  # noqa: F401
        from . import company as _models_company  # noqa: F401
        from . import customer as _models_customer  # noqa: F401
        from . import product as _models_product  # noqa: F401
        from . import user as _models_user  # noqa: F401
        from . import warehouse as _models_warehouse  # noqa: F401

        # OTP подгружаем опционально, если ранее не была смэплена таблица otp_codes
        if not _is_table_mapped("otp_codes"):
            from . import otp as _models_otp  # noqa: F401
        # order специально НЕ импортируем жёстко — если он битый, заглушка уже установлена выше
except Exception as _implicit_import_err:
    logger.debug("Optional side-imports failed: %s", _implicit_import_err)

# Финальный авто-догрузчик (если есть _loader.py — используем его, иначе pkgutil).
try:
    if not RUNTIME_OPTS["DISABLE_AUTOIMPORT"]:
        if _auto_import_all_models:
            _auto_import_all_models(log=False)
        else:
            discover_and_import_all_model_modules(with_billing=False)
except Exception as _auto_err:
    logger.debug("auto-import models at package import failed: %s", _auto_err)
