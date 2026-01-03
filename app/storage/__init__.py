# app/storage/__init__.py
"""
Storage backends package.

Назначение:
- Единая точка импорта стораджей (SQL-бекенды и будущие адаптеры).
- Безопасные ре-экспорты классов, если модуль существует.
- Ленивые фабрики и синглтоны для повторного использования соединений.
- Утилиты для обзора доступных стораджей и принудительной инициализации схемы.

Совместимость:
- Сохраняем прямой импорт: `from app.storage.campaigns_sql import CampaignsStorageSQL`
- И одновременно поддерживаем: `from app.storage import CampaignsStorageSQL, get_storage, get_wallet_storage, ...`

Окружение:
- SMARTSELL_STORAGE_DEFAULT_BACKEND (по умолчанию "sql")
"""

from __future__ import annotations

import importlib
import logging
import os
from types import ModuleType
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 0) Внутренние утилиты
# ---------------------------------------------------------------------------


def _lazy_import(module: str) -> ModuleType:
    """Безопасный ленивый импорт с логированием."""
    try:
        return importlib.import_module(module)
    except Exception as e:
        logger.debug("lazy import failed for %s: %s", module, e)
        raise


def _maybe_import_class(module: str, class_name: str) -> Optional[type[Any]]:
    """
    Пытается импортировать класс <class_name> из модуля <module>.
    Возвращает класс или None (ничего не валит).
    """
    try:
        mod = importlib.import_module(module)
        cls = getattr(mod, class_name, None)
        if cls is None:
            logger.debug("%s does not expose %s", module, class_name)
            return None
        return cls  # type: ignore[return-value]
    except Exception as e:
        logger.debug("optional import failed for %s.%s: %s", module, class_name, e)
        return None


# ---------------------------------------------------------------------------
# 1) Пытаемся импортировать известные SQL стораджи. Ничего не падает — логируем и идём дальше.
# ---------------------------------------------------------------------------

# Campaigns
_CampaignsStorageSQL: Optional[type[Any]] = _maybe_import_class("app.storage.campaigns_sql", "CampaignsStorageSQL")

# Wallet
_WalletStorageSQL: Optional[type[Any]] = _maybe_import_class("app.storage.wallet_sql", "WalletStorageSQL")

# Products (если есть)
_ProductsStorageSQL: Optional[type[Any]] = _maybe_import_class("app.storage.products_sql", "ProductsStorageSQL")

# Payments (если есть)
_PaymentsStorageSQL: Optional[type[Any]] = _maybe_import_class("app.storage.payments_sql", "PaymentsStorageSQL")

# ---------------------------------------------------------------------------
# 2) Публичные ре-экспорты: только те, что реально доступны.
# ---------------------------------------------------------------------------

__all__: list[str] = [
    # функции фабрики/утилит (экспортируются всегда)
    "list_available_storages",
    "get_storage",
    "get_wallet_storage",
    "get_campaigns_storage",
    "get_products_storage",
    "get_payments_storage",
    "ensure_all_schemas",
    "ensure_schema_for",
    "clear_storage_singletons",
    "get_default_backend",
    "set_default_backend",
    "debug_dump_registry",
]

if _CampaignsStorageSQL is not None:
    CampaignsStorageSQL = _CampaignsStorageSQL  # type: ignore[assignment]
    __all__.append("CampaignsStorageSQL")

if _WalletStorageSQL is not None:
    WalletStorageSQL = _WalletStorageSQL  # type: ignore[assignment]
    __all__.append("WalletStorageSQL")

if _ProductsStorageSQL is not None:
    ProductsStorageSQL = _ProductsStorageSQL  # type: ignore[assignment]
    __all__.append("ProductsStorageSQL")

if _PaymentsStorageSQL is not None:
    PaymentsStorageSQL = _PaymentsStorageSQL  # type: ignore[assignment]
    __all__.append("PaymentsStorageSQL")

# ---------------------------------------------------------------------------
# 3) Реестр доступных стораджей и фабрики (с синглтонами).
# ---------------------------------------------------------------------------

_DEFAULT_BACKEND = (os.getenv("SMARTSELL_STORAGE_DEFAULT_BACKEND") or "sql").strip().lower() or "sql"


def get_default_backend() -> str:
    return _DEFAULT_BACKEND


def set_default_backend(backend: str) -> None:
    """Позволяет сменить дефолт на лету (например, в тестах)."""
    global _DEFAULT_BACKEND
    _DEFAULT_BACKEND = (backend or "sql").strip().lower() or "sql"


# Ключи реестра: (service, backend) -> класс
# service: "campaigns" | "wallet" | "products" | "payments"
_STORAGE_REGISTRY: dict[tuple[str, str], type[Any]] = {}

if _CampaignsStorageSQL is not None:
    _STORAGE_KEY = ("campaigns", "sql")
    _STORAGE_REGISTRY[_STORAGE_KEY] = _CampaignsStorageSQL
    del _STORAGE_KEY

if _WalletStorageSQL is not None:
    _STORAGE_KEY = ("wallet", "sql")
    _STORAGE_REGISTRY[_STORAGE_KEY] = _WalletStorageSQL
    del _STORAGE_KEY

if _ProductsStorageSQL is not None:
    _STORAGE_KEY = ("products", "sql")
    _STORAGE_REGISTRY[_STORAGE_KEY] = _ProductsStorageSQL
    del _STORAGE_KEY

if _PaymentsStorageSQL is not None:
    _STORAGE_KEY = ("payments", "sql")
    _STORAGE_REGISTRY[_STORAGE_KEY] = _PaymentsStorageSQL
    del _STORAGE_KEY

# Синглтоны по (service, backend)
_SINGLETONS: dict[tuple[str, str], Any] = {}


def list_available_storages() -> dict[str, list[str]]:
    """
    Возвращает доступные бекенды по сервисам.
    Пример: {"wallet": ["sql"], "campaigns": ["sql"]}
    """
    out: dict[str, list[str]] = {}
    for service, backend in _STORAGE_REGISTRY.keys():
        out.setdefault(service, []).append(backend)
    for k in out:
        out[k].sort()
    return out


def _resolve_storage_class(service: str, backend: Optional[str]) -> type[Any]:
    svc = (service or "").strip().lower()
    bkd = (backend or _DEFAULT_BACKEND).strip().lower()
    key = (svc, bkd)
    cls = _STORAGE_REGISTRY.get(key)
    if cls is None:
        raise LookupError(
            f"Storage not available for service='{svc}', backend='{bkd}'. " f"Available: {list_available_storages()}"
        )
    return cls


def get_storage(
    service: str,
    backend: Optional[str] = None,
    *,
    force_new: bool = False,
    **kwargs: Any,
) -> Any:
    """
    Ленивая фабрика стораджей с кэшем-синглтоном.
    - service: "campaigns" | "wallet" | "products" | "payments"
    - backend: "sql" (по умолчанию SMARTSELL_STORAGE_DEFAULT_BACKEND или "sql")
    - force_new: True — всегда создавать новый инстанс (не из синглтона)
    - kwargs: пробрасываются в конструктор стораджа (например, кастомные параметры)

    Raises:
      LookupError — если сторадж/бекенд недоступен.
    """
    svc = (service or "").strip().lower()
    bkd = (backend or _DEFAULT_BACKEND).strip().lower()
    key = (svc, bkd)

    cls = _resolve_storage_class(svc, bkd)

    if not force_new:
        inst = _SINGLETONS.get(key)
        if inst is not None:
            return inst

    instance = cls(**kwargs) if kwargs else cls()
    if not force_new:
        _SINGLETONS[key] = instance
    return instance


# Удобные шорткаты — читаются лучше в роутерах/сервисах
def get_wallet_storage(backend: Optional[str] = None, *, force_new: bool = False, **kwargs: Any) -> Any:
    return get_storage("wallet", backend=backend, force_new=force_new, **kwargs)


def get_campaigns_storage(backend: Optional[str] = None, *, force_new: bool = False, **kwargs: Any) -> Any:
    return get_storage("campaigns", backend=backend, force_new=force_new, **kwargs)


def get_products_storage(backend: Optional[str] = None, *, force_new: bool = False, **kwargs: Any) -> Any:
    return get_storage("products", backend=backend, force_new=force_new, **kwargs)


def get_payments_storage(backend: Optional[str] = None, *, force_new: bool = False, **kwargs: Any) -> Any:
    return get_storage("payments", backend=backend, force_new=force_new, **kwargs)


def clear_storage_singletons(*, services: Optional[list[str]] = None) -> None:
    """
    Сбрасывает кэш синглтонов. Полезно для тестов/канареек.
    Если services не указан — чистим всё.
    """
    if not services:
        _SINGLETONS.clear()
        return
    to_del = {(svc, bkd) for (svc, bkd) in _SINGLETONS.keys() if svc in {s.lower() for s in services}}
    for k in to_del:
        _SINGLETONS.pop(k, None)


def debug_dump_registry() -> dict[str, dict[str, str]]:
    """
    Диагностическая сводка: какие сервисы зарегистрированы, из каких модулей и классов.
    """
    out: dict[str, dict[str, str]] = {}
    for (svc, bkd), cls in _STORAGE_REGISTRY.items():
        out.setdefault(svc, {})[bkd] = f"{cls.__module__}.{cls.__name__}"
    return out


# ---------------------------------------------------------------------------
# 4) Инициализация схем БД «по требованию»
#    Если модуль стораджа предоставляет ensure_schema/_ensure_schema — вызовем.
# ---------------------------------------------------------------------------


def _try_call_schema_initializer(module_obj: Any) -> None:
    for name in ("ensure_schema", "_ensure_schema"):
        fn = getattr(module_obj, name, None)
        if callable(fn):
            try:
                fn()
                logger.info("Schema initializer called: %s.%s()", getattr(module_obj, "__name__", module_obj), name)
                return
            except Exception as e:
                logger.warning(
                    "Schema initializer failed for %s.%s(): %s", getattr(module_obj, "__name__", module_obj), name, e
                )


def ensure_schema_for(service: str, backend: Optional[str] = None) -> None:
    """
    Принудительно вызывает инициализацию схемы для конкретного сервиса/бекенда,
    если модуль её экспортирует.
    """
    svc = (service or "").strip().lower()
    bkd = (backend or _DEFAULT_BACKEND).strip().lower()

    # Пытаемся импортировать модуль стораджа по ключу
    cls = _resolve_storage_class(svc, bkd)
    try:
        mod = importlib.import_module(cls.__module__)
    except Exception as e:
        logger.debug("ensure_schema_for: cannot import module for %s/%s: %s", svc, bkd, e)
        return
    _try_call_schema_initializer(mod)


def ensure_all_schemas() -> None:
    """
    Пытается прогнать инициализацию схемы для всех подключённых стораджей.
    Без падений — максимум предупреждение в лог.
    """
    # Кампании
    if _CampaignsStorageSQL is not None:
        try:
            _mod_campaigns = _lazy_import("app.storage.campaigns_sql")
            _try_call_schema_initializer(_mod_campaigns)
        except Exception as e:
            logger.debug("ensure schema: campaigns skipped: %s", e)

    # Кошельки
    if _WalletStorageSQL is not None:
        try:
            _mod_wallet = _lazy_import("app.storage.wallet_sql")
            _try_call_schema_initializer(_mod_wallet)
        except Exception as e:
            logger.debug("ensure schema: wallet skipped: %s", e)

    # Товары
    if _ProductsStorageSQL is not None:
        try:
            _mod_products = _lazy_import("app.storage.products_sql")
            _try_call_schema_initializer(_mod_products)
        except Exception as e:
            logger.debug("ensure schema: products skipped: %s", e)

    # Платежи
    if _PaymentsStorageSQL is not None:
        try:
            _mod_payments = _lazy_import("app.storage.payments_sql")
            _try_call_schema_initializer(_mod_payments)
        except Exception as e:
            logger.debug("ensure schema: payments skipped: %s", e)


# ---------------------------------------------------------------------------
# 5) Backwards compatibility (как было в исходнике) — уже обеспечено выше
#    (классы экспортируются, если доступны).
# ---------------------------------------------------------------------------
