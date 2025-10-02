# app/routers/__init__.py
from __future__ import annotations
"""
Routers package initialization (enterprise-grade).

Возможности:
- Автоматическое подключение всех модулей с APIRouter из пакета `app.routers`.
- Поддержка ручных оверрайдов/приоритизации через ROUTER_SPECS.
- Единый API-префикс из settings.API_V1_STR (по умолчанию /api/v1).
- Фильтрация через ENV: ROUTERS_INCLUDE / ROUTERS_EXCLUDE (поддержка масок `*`).
- Health endpoints: /livez, /readyz, /metrics (Prometheus при наличии prometheus_client).
"""

import fnmatch
import importlib
import logging
import os
import pkgutil
import sys
from dataclasses import dataclass
from types import ModuleType
from typing import Dict, Iterable, List, Optional, Set, Tuple

from fastapi import APIRouter, FastAPI, Response
from fastapi.responses import PlainTextResponse

try:
    from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, generate_latest  # type: ignore
    _PROM_AVAILABLE = True
except Exception:  # pragma: no cover
    _PROM_AVAILABLE = False

try:
    # единый источник правды по настройкам
    from app.core.config import settings  # type: ignore
except Exception as e:  # pragma: no cover
    raise RuntimeError("Cannot import settings from app.core.config") from e

logger = logging.getLogger(__name__)


# -------------------------------------------------------------------------
# Спецификация ручных подключений (имеют приоритет над автодискавери)
# -------------------------------------------------------------------------
@dataclass(frozen=True)
class RouterSpec:
    module: str                 # e.g. "app.routers.auth"
    attr: str = "router"        # имя APIRouter в модуле
    prefix: Optional[str] = None
    tags: Optional[List[str]] = None
    enabled: bool = True        # можно временно отключить модуль


# При необходимости переопредели префиксы/теги тут:
ROUTER_SPECS: Tuple[RouterSpec, ...] = (
    RouterSpec("app.routers.auth",       prefix="/auth",       tags=["auth"]),
    RouterSpec("app.routers.products",   prefix="/products",   tags=["products"]),
    RouterSpec("app.routers.orders",     prefix="/orders",     tags=["orders"]),
    RouterSpec("app.routers.payments",   prefix="/payments",   tags=["payments"]),
    RouterSpec("app.routers.warehouses", prefix="/warehouses", tags=["warehouses"]),
    RouterSpec("app.routers.analytics",  prefix="/analytics",  tags=["analytics"]),
)

# Имя атрибута по умолчанию, который должен экспортировать APIRouter
DEFAULT_ROUTER_ATTR = "router"

# Модуль пакета, который сканируем
ROOT_PACKAGE = "app.routers"


# -------------------------------------------------------------------------
# Utility: API base prefix
# -------------------------------------------------------------------------
def _api_prefix() -> str:
    base = getattr(settings, "API_V1_STR", "/api/v1") or "/api/v1"
    if not base.startswith("/"):
        base = "/" + base
    return base.rstrip("/")


# -------------------------------------------------------------------------
# ENV filters: include/exclude с масками
# -------------------------------------------------------------------------
def _parse_csv_env(name: str) -> List[str]:
    raw = os.getenv(name, "").strip()
    return [x.strip() for x in raw.split(",") if x.strip()]


def _match_any(name: str, patterns: Iterable[str]) -> bool:
    return any(fnmatch.fnmatch(name, p) for p in patterns)


def _should_include(module_name: str, includes: List[str], excludes: List[str]) -> bool:
    if includes and not _match_any(module_name, includes):
        return False
    if excludes and _match_any(module_name, excludes):
        return False
    return True


# -------------------------------------------------------------------------
# Discovery: обходим пакет и ищем модули с APIRouter
# -------------------------------------------------------------------------
def _iter_modules(package: str) -> Iterable[str]:
    """Вернёт полные имена модулей внутри пакета (включая подпакеты)."""
    try:
        pkg = importlib.import_module(package)
    except Exception as e:  # pragma: no cover
        logger.warning("Failed to import package %s: %s", package, e)
        return []

    if not hasattr(pkg, "__path__"):
        return []

    for module_info in pkgutil.walk_packages(pkg.__path__, pkg.__name__ + "."):
        yield module_info.name


def _import_module(module_name: str) -> Optional[ModuleType]:
    try:
        return importlib.import_module(module_name)
    except Exception as e:
        logger.warning("Module import failed: %s (%s)", module_name, e)
        return None


def _extract_router_info(mod: ModuleType) -> Optional[Tuple[APIRouter, str, List[str]]]:
    """
    Ищем APIRouter и метаданные префикса/тегов в модуле.
    Поддерживаются необязательные экспортируемые переменные:
      - ROUTER_PREFIX: str
      - ROUTER_TAGS: list[str]
      - ROUTER_ATTR: str (если имя APIRouter не 'router')
    """
    # на случай нестандартного имени
    attr_name = getattr(mod, "ROUTER_ATTR", DEFAULT_ROUTER_ATTR)
    router = getattr(mod, attr_name, None)
    if not isinstance(router, APIRouter):
        return None

    # берём префикс из модуля, либо строим из имени файла
    prefix = getattr(mod, "ROUTER_PREFIX", None)
    if not prefix:
        # пример: app.routers.order_items -> "order-items"
        module_leaf = mod.__name__.split(".")[-1]
        safe = module_leaf.replace("_", "-")
        prefix = f"/{safe}"

    tags = getattr(mod, "ROUTER_TAGS", None) or list(router.tags or [])
    if not tags:
        tags = [prefix.strip("/")]  # простая эвристика

    return router, prefix, tags


# -------------------------------------------------------------------------
# Загрузка роутеров: ручные + автосканирование
# -------------------------------------------------------------------------
def load_all_routers() -> Dict[str, Tuple[APIRouter, str, List[str]]]:
    """
    Собирает все доступные роутеры.
    Приоритет: ROUTER_SPECS (enabled=True) → autodiscover.
    Возвращает словарь:
      { module_name: (router, module_prefix, tags) }
    """
    includes = _parse_csv_env("ROUTERS_INCLUDE")
    excludes = _parse_csv_env("ROUTERS_EXCLUDE")

    loaded: Dict[str, Tuple[APIRouter, str, List[str]]] = {}
    seen_modules: Set[str] = set()

    # 1) Ручные спецификации
    for spec in ROUTER_SPECS:
        if not spec.enabled:
            continue
        if not _should_include(spec.module, includes, excludes):
            logger.info("Router excluded by filter: %s", spec.module)
            continue

        mod = _import_module(spec.module)
        if not mod:
            continue

        # ищем APIRouter по указанному имени
        router = getattr(mod, spec.attr, None)
        if not isinstance(router, APIRouter):
            logger.warning("No APIRouter attr=%s in %s", spec.attr, spec.module)
            continue

        # префикс/теги: spec → модуль → эвристика
        module_prefix = spec.prefix or getattr(mod, "ROUTER_PREFIX", None)
        if not module_prefix:
            leaf = spec.module.split(".")[-1]
            module_prefix = "/" + leaf.replace("_", "-")

        tags = spec.tags or getattr(mod, "ROUTER_TAGS", None) or list(router.tags or [])
        if not tags:
            tags = [module_prefix.strip("/")]

        loaded[spec.module] = (router, module_prefix, tags)
        seen_modules.add(spec.module)
        logger.debug("Router loaded (spec): %s", spec.module)

    # 2) Автодискавери
    for module_name in _iter_modules(ROOT_PACKAGE):
        if module_name in seen_modules:
            continue
        if not _should_include(module_name, includes, excludes):
            logger.info("Router excluded by filter: %s", module_name)
            continue

        mod = _import_module(module_name)
        if not mod:
            continue

        info = _extract_router_info(mod)
        if not info:
            continue

        loaded[module_name] = info
        seen_modules.add(module_name)
        logger.debug("Router discovered: %s", module_name)

    return loaded


# -------------------------------------------------------------------------
# Регистрация в FastAPI
# -------------------------------------------------------------------------
def register_routers(app: FastAPI) -> None:
    """
    Подключает все найденные роутеры к приложению.
    """
    base_prefix = _api_prefix()
    collected = load_all_routers()
    if not collected:
        logger.warning("No routers collected from %s", ROOT_PACKAGE)

    for module_name, (router, module_prefix, tags) in collected.items():
        # аккуратно склеиваем префиксы
        mp = (module_prefix or "").strip()
        if mp and not mp.startswith("/"):
            mp = "/" + mp
        full_prefix = f"{base_prefix}{mp}"

        try:
            app.include_router(router, prefix=full_prefix, tags=tags)
            logger.info("Router registered: module=%s prefix=%s tags=%s", module_name, full_prefix, tags)
        except Exception as e:
            logger.warning("Router registration failed: module=%s error=%s", module_name, e)

    # health/metrics
    register_health(app)


# -------------------------------------------------------------------------
# Health & Metrics
# -------------------------------------------------------------------------
def register_health(app: FastAPI) -> None:
    """
    Регистрирует /livez, /readyz и /metrics (если есть prometheus_client).
    - /livez — просто жив ли процесс.
    - /readyz — базовые проверки готовности (settings.health_check, БД если доступно).
    - /metrics — Prometheus endpoint.
    """
    @app.get("/livez", include_in_schema=False)
    def livez() -> Response:
        return PlainTextResponse("OK", status_code=200)

    @app.get("/readyz", include_in_schema=False)
    def readyz() -> Response:
        errors: List[str] = []

        # settings.health_check(), если реализовано
        try:
            hc = getattr(settings, "health_check", None)
            if callable(hc):
                res = hc()
                if not res.get("ok", True):
                    errors.append("settings: " + "; ".join(res.get("errors", []) or []))
        except Exception as e:  # pragma: no cover
            errors.append(f"settings.health_check failed: {e}")

        # db health, если есть helper
        try:
            from app.core.db import health_check_db  # type: ignore
            db_res = health_check_db()
            if not db_res.get("ok", True):
                errors.append("db: " + (db_res.get("error") or "unknown error"))
        except Exception as e:  # pragma: no cover
            # БД может быть асинхронной/в другом модуле — не заваливаем readiness
            logger.debug("DB health check skipped/failed: %s", e)

        if errors:
            return PlainTextResponse("NOT_READY\n" + "\n".join(errors), status_code=503)
        return PlainTextResponse("READY", status_code=200)

    if _PROM_AVAILABLE:
        registry = CollectorRegistry()  # кастомный, если нужно — иначе можно оставить по умолчанию

        @app.get("/metrics", include_in_schema=False)
        def metrics() -> Response:
            try:
                # Если используете default REGISTRY, просто уберите registry=...
                output = generate_latest(registry)
                return Response(content=output, media_type=CONTENT_TYPE_LATEST)
            except Exception as e:  # pragma: no cover
                logger.warning("Prometheus metrics failed: %s", e)
                return PlainTextResponse("metrics unavailable", status_code=503)
    else:
        logger.info("prometheus_client not installed; /metrics will not be exposed")


__all__ = [
    "RouterSpec",
    "ROUTER_SPECS",
    "register_routers",
    "load_all_routers",
    "register_health",
]
