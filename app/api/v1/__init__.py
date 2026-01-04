"""
API v1 package with dynamic router loading (production-ready).

- Динамически подключает все v1-роутеры (auth, users, products, campaigns, kaspi и др.).
- Не дублирует префикс /api/v1 (умная проверка префикса дочерних роутеров).
- Диагностика: /api/v1/health, /api/v1/_debug/routers, /api/v1/_debug/routes.
- Экспортирует готовый APIRouter как `api_v1` для использования в app/main.py.
- Позволяет регистрировать дополнительные модули через register_extra_router_module().

Совместимость:
- Если в проекте есть старые роутеры в пакете app.api.routes (например, app/api/routes/campaign.py),
  их можно подключить через register_extra_router_module("app.api.routes.campaign") — префиксы будут
  смонтированы аккуратно (без двойного /api/v1).

Замечания:
- Модуль терпимо относится к отсутствию settings/get_logger в ранних стадиях работы.
- Не задаёт единый prefix у корневого APIRouter, чтобы корректно смешивать абсолютные/относительные префиксы.
"""

from __future__ import annotations

import importlib
import pkgutil
import time
from collections.abc import Iterable
from types import ModuleType
from typing import Final

from fastapi import APIRouter
from fastapi.responses import JSONResponse

# -----------------------------
# Конфигурация и логгер (fail-soft)
# -----------------------------
try:
    # settings может отсутствовать на ранних этапах; берём API_V1_STR (как в .env) или API_V1_PREFIX.
    from app.core.config import settings  # type: ignore

    _API_V1_PREFIX: str = getattr(settings, "API_V1_STR", None) or getattr(settings, "API_V1_PREFIX", "/api/v1")
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
        _logging.basicConfig(
            level=_logging.INFO,
            format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        )

# -----------------------------
# Список модулей с роутерами
# -----------------------------
# Порядок важен: базовые/авторизационные раньше прикладных.
DEFAULT_ROUTER_MODULES: Final[list[str]] = [
    "app.api.v1.auth",
    "app.api.v1.users",
    "app.api.v1.products",
    "app.api.v1.campaigns",
    "app.api.v1.wallet",
    "app.api.v1.payments",
    "app.api.v1.subscriptions",
    "app.api.v1.analytics",
    # Kaspi API (обязательно включаем, чтобы появился /api/v1/kaspi/*)
    "app.api.v1.kaspi",
]

# Дополнительно можно автодобавлять модули позже (через фичефлаги/настройки/код).
EXTRA_ROUTER_MODULES: list[str] = []


def register_extra_router_module(module_name: str) -> None:
    """
    Зарегистрировать модуль с роутером динамически (например, из main.py или при инициализации пакета).
    Пример: register_extra_router_module("app.api.routes.campaign")
    """
    if not module_name:
        return
    if module_name not in EXTRA_ROUTER_MODULES:
        EXTRA_ROUTER_MODULES.append(module_name)
        logger.info("Registered extra router module: %s", module_name)


# -----------------------------
# Внутренние утилиты
# -----------------------------
def _include_router_safely(parent: APIRouter, child: APIRouter, api_prefix: str) -> None:
    """
    Включает дочерний роутер так, чтобы не получилось двойного префикса.
    Если child.prefix уже начинается с /api/v1 — подключаем без дополнительного префикса.
    Если child.prefix пустой или относительный — «подвешиваем» его под /api/v1.
    Если child.prefix начинается с /api (но не /api/v1) — оставляем как есть (совместимость со старыми роутерами).
    """
    child_prefix = (child.prefix or "").strip() or ""
    normalized_api_prefix = api_prefix.rstrip("/")

    # Абсолютный и уже v1 — монтируем как есть
    if child_prefix.startswith(normalized_api_prefix):
        parent.include_router(child, prefix="")
        logger.debug("Included router as-is (absolute v1 prefix): %s", child_prefix)
        return

    # Абсолютный под /api, но не /api/v1 — не трогаем (legacy-совместимость)
    if child_prefix.startswith("/api"):
        parent.include_router(child, prefix="")
        logger.debug("Included router as-is (absolute legacy prefix): %s", child_prefix)
        return

    # Относительный — подвешиваем под /api/v1
    parent.include_router(child, prefix=api_prefix)
    logger.debug("Included router with base prefix '%s': %s", api_prefix, child_prefix)


def _load_module(module_name: str) -> ModuleType | None:
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
    """
    Итерация по списку модулей: сначала дефолтные, затем EXTRA. Дубликаты исключаются.
    """
    seen: set[str] = set()
    for name in DEFAULT_ROUTER_MODULES + list(EXTRA_ROUTER_MODULES):
        if not name or name in seen:
            continue
        seen.add(name)
        yield name


def _autodiscover_under(package_name: str) -> list[str]:
    """
    Опциональная автодисковер-поддержка: найти подмодули, в которых может быть `router`.
    По умолчанию не подключает автоматически — просто возвращает список имён.
    Можно использовать в будущем, если захотим включать всё из app.api.v1.* без ручного списка.
    """
    discovered: list[str] = []
    try:
        pkg = importlib.import_module(package_name)
        if not hasattr(pkg, "__path__"):
            return discovered
        for mod_info in pkgutil.iter_modules(pkg.__path__, prefix=f"{package_name}."):
            discovered.append(mod_info.name)
    except Exception as e:
        logger.debug("Autodiscover under %s failed: %s", package_name, e)
    return discovered


def _routes_snapshot(router: APIRouter) -> list[dict[str, str]]:
    """
    Снимок путей для отладки: метод + путь + имя хэндлера.
    Работает на уровне APIRouter (без необходимости иметь весь app).
    """
    snapshot: list[dict[str, str]] = []
    for r in router.routes:
        try:
            methods = ",".join(sorted(getattr(r, "methods", []) or []))
            path = getattr(r, "path", "")
            name = getattr(r, "name", "") or getattr(getattr(r, "endpoint", None), "__name__", "")
            snapshot.append({"methods": methods, "path": path, "name": name})
        except Exception:
            continue
    return snapshot


# -----------------------------
# Создание корневого v1-роутера
# -----------------------------
def create_api_router() -> APIRouter:
    """
    Create API router with all v1 endpoints (idempotent).
    ВАЖНО: тут не задаём общий prefix — чтобы умно соединять абсолютные/относительные префиксы дочерних роутеров.
    """
    api_router = APIRouter()
    registered: list[str] = []
    skipped: list[str] = []

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

    # Диагностика верхнего уровня v1 (без зависимости от конкретных модулей).
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

    @diag.get("/_debug/routes", response_class=JSONResponse, summary="List mounted v1 routes")
    def list_mounted_routes():
        return _routes_snapshot(api_router)

    api_router.include_router(diag)  # эти пути всегда под /api/v1

    # Итоговый лог (удобно видеть при старте приложения)
    logger.info(
        "API v1 mounted. prefix=%s registered=%d skipped=%d",
        _API_V1_PREFIX,
        len(registered),
        len(skipped),
    )
    if skipped:
        logger.warning("Skipped v1 routers: %s", ", ".join(skipped))

    return api_router


# Экспорт готового роутера для main.py
api_v1: APIRouter = create_api_router()

__all__ = [
    "create_api_router",
    "api_v1",
    "DEFAULT_ROUTER_MODULES",
    "EXTRA_ROUTER_MODULES",
    "register_extra_router_module",
]
