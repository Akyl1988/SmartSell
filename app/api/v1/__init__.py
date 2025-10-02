"""
API v1 package with dynamic router loading (production-ready).

- Динамически подключает все v1-роутеры (в т.ч. campaigns).
- Не дублирует префикс /api/v1 (умная проверка префикса дочерних роутеров).
- Даёт диагностические эндпоинты: /api/v1/health и /api/v1/_debug/routers.
- Экспортирует готовый APIRouter как `api_v1` для использования в app/main.py.
"""

from __future__ import annotations

import importlib
import time
from types import ModuleType
from typing import Iterable, List, Optional

from fastapi import APIRouter
from fastapi.responses import JSONResponse

try:
    # settings может отсутствовать на ранних этапах, подстрахуемся
    from app.core.config import settings  # type: ignore
    _API_V1_PREFIX: str = getattr(settings, "API_V1_PREFIX", "/api/v1") or "/api/v1"
except Exception:
    _API_V1_PREFIX = "/api/v1"

try:
    from app.core.logging import get_logger  # type: ignore
    logger = get_logger(__name__)
except Exception:
    # минимальный fallback логгер
    import logging as _logging

    logger = _logging.getLogger(__name__)
    if not logger.handlers:
        _logging.basicConfig(level=_logging.INFO, format="%(asctime)s %(levelname)s [%(name)s] %(message)s")


# Явный список v1-модулей с роутерами.
# Порядок важен, чтобы базовые/авторизационные шли раньше.
ROUTER_MODULES: List[str] = [
    "app.api.v1.auth",
    "app.api.v1.users",
    "app.api.v1.products",
    # ВАЖНО: добавили campaigns — тесты ждут /api/v1/campaigns
    "app.api.v1.campaigns",
]

# Дополнительно можно автодобавлять модули позже (через фичефлаги/настройки).
EXTRA_ROUTER_MODULES: List[str] = []


def _include_router_safely(parent: APIRouter, child: APIRouter, api_prefix: str) -> None:
    """
    Включает дочерний роутер так, чтобы не получилось двойного префикса.
    Если child.prefix уже начинается с /api/v1 — подключаем без дополнительного префикса.
    Иначе — «подвешиваем» его под /api/v1.
    """
    child_prefix = (child.prefix or "").strip() or ""
    if child_prefix.startswith(api_prefix.rstrip("/")):
        # префикс уже абсолютный -> вешаем как есть
        parent.include_router(child, prefix="")
        logger.debug("Included router as-is (absolute prefix): %s", child_prefix)
    else:
        # префикс относительный -> добавим базовый /api/v1
        parent.include_router(child, prefix=api_prefix)
        logger.debug("Included router with base prefix '%s': %s", api_prefix, child_prefix)


def _load_module(module_name: str) -> Optional[ModuleType]:
    try:
        t0 = time.perf_counter()
        module = importlib.import_module(module_name)
        dt = (time.perf_counter() - t0) * 1000
        logger.info("Loaded router module %s (%.1f ms)", module_name, dt)
        return module
    except ImportError as e:
        logger.error("Failed to import router module %s: %s", module_name, e)
    except Exception as e:
        logger.error("Error loading router from %s: %s", module_name, e)
    return None


def _iter_modules() -> Iterable[str]:
    # основная последовательность + дополнительные из настроек/кода
    seen = set()
    for name in ROUTER_MODULES + list(EXTRA_ROUTER_MODULES):
        if not name or name in seen:
            continue
        seen.add(name)
        yield name


def create_api_router() -> APIRouter:
    """Create API router with all v1 endpoints (idempotent)."""
    # ВАЖНО: тут не задаём общий prefix — чтобы умно соединять абсолютные/относительные префиксы дочерних роутеров.
    api_router = APIRouter()
    registered: List[str] = []
    skipped: List[str] = []

    for module_name in _iter_modules():
        module = _load_module(module_name)
        if not module:
            skipped.append(module_name)
            continue

        router = getattr(module, "router", None)
        if router is None or not isinstance(router, APIRouter):
            logger.warning("No router found in %s", module_name)
            skipped.append(module_name)
            continue

        _include_router_safely(api_router, router, _API_V1_PREFIX)
        registered.append(f"{module_name}:{router.prefix or ''}")

    # Диагностические эндпоинты верхнего уровня v1 (без зависимости от кампаний и т.п.)
    diag = APIRouter(prefix=_API_V1_PREFIX, tags=["diagnostics"])

    @diag.get("/health", summary="API v1 health")
    def api_v1_health():
        return {
            "status": "ok",
            "prefix": _API_V1_PREFIX,
            "registered": registered,
            "skipped": skipped,
            "time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }

    @diag.get("/_debug/routers", response_class=JSONResponse, summary="List loaded v1 routers")
    def list_loaded_routers():
        return {"registered": registered, "skipped": skipped}

    api_router.include_router(diag)  # эти пути всегда под /api/v1

    return api_router


# Экспорт готового роутера для main.py:
api_v1: APIRouter = create_api_router()

__all__ = ["create_api_router", "api_v1", "ROUTER_MODULES", "EXTRA_ROUTER_MODULES"]
