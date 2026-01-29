from __future__ import annotations

"""
Единая точка агрегации и подключения API-роутеров (v1) + диагностика.

Возможности:
- Экспорт v1-модулей: auth, users, products, campaigns, payments, wallet, kaspi.
- Алиас 'billing' -> campaigns (обратная совместимость).
- Реестр V1_ROUTERS c «умным» монтированием (учёт абсолютного/относительного префикса).
- Защита от двойного include (даже при повторных вызовах).
- Безопасные опциональные импорты wallet/payments/kaspi.
- Диагностика:
  * /api/v1/_debug/routers — посмотреть зарегистрированные роутеры (что выискалось и как смонтировано).
  * /api/v1/health-v1 — простой health v1-сборки.

Использование в app.main:
    from app.api.routes import mount_v1
    mount_v1(app, base_prefix="/api/v1")
"""

import logging
import os
import time
from typing import Any, Union

from fastapi import APIRouter, FastAPI
from fastapi.responses import JSONResponse

from app.core import config

logger = logging.getLogger(__name__)


_IMPORT_TIMINGS_MS: dict[str, float] = {}


# ------------------------------------------------------------------------------
# Помощники импорта
# ------------------------------------------------------------------------------
def _is_test_or_ci_mode() -> bool:
    if os.getenv("GITHUB_ACTIONS") == "true":
        return True
    if os.getenv("CI") == "true":
        return True
    if os.getenv("PYTEST_CURRENT_TEST"):
        return True
    env = (os.getenv("ENVIRONMENT") or "").strip().lower()
    if env == "testing":
        return True
    testing = (os.getenv("TESTING") or "").strip().lower()
    if testing in ("1", "true", "yes", "on"):
        return True
    return False


_STRICT_IMPORTS_IN_TESTS = {
    "app.api.v1.wallet",
    "app.api.v1.payments",
    "app.api.v1.subscriptions",
    "app.api.v1.invoices",
}


def _try_import(path: str) -> Any | None:
    try:
        t0 = time.perf_counter()
        mod = __import__(path, fromlist=["router"])
        _IMPORT_TIMINGS_MS[path] = (time.perf_counter() - t0) * 1000
        return mod
    except Exception:
        if _is_test_or_ci_mode() and path in _STRICT_IMPORTS_IN_TESTS:
            raise
        _IMPORT_TIMINGS_MS[path] = -1.0
        return None


# ------------------------------------------------------------------------------
# Базовые v1-модули (считаем обязательными в проекте)
# ------------------------------------------------------------------------------
auth_mod = _try_import("app.api.v1.auth")
users_mod = _try_import("app.api.v1.users")
products_mod = _try_import("app.api.v1.products")
campaigns_mod = _try_import("app.api.v1.campaigns")
analytics_mod = _try_import("app.api.v1.analytics")

# Опциональные
wallet = _try_import("app.api.v1.wallet")
payments = _try_import("app.api.v1.payments")
invoices = _try_import("app.api.v1.invoices")
kaspi_mod = _try_import("app.api.v1.kaspi")  # ⬅ добавлено
integrations_mod = _try_import("app.api.v1.integrations")
debug_db_mod = _try_import("app.api.v1.debug_db")

# Переэкспорт удобных имён/алиасов (для внешнего кода)
auth = auth_mod
users = users_mod
products = products_mod
campaigns = campaigns_mod
billing = campaigns_mod  # исторический алиас
kaspi = kaspi_mod  # ⬅ добавлено
analytics = analytics_mod

__all__ = [
    "auth",
    "users",
    "products",
    "campaigns",
    "billing",
    "wallet",
    "payments",
    "kaspi",  # ⬅ добавлено
    "analytics",
    "V1_ROUTERS",
    "register_v1_router",
    "register_optional_v1_router",
    "include_router_smart",
    "mount_v1",
    "mount_all",
    "mount_into_router",
    "get_v1_registry",
    "diagnose_v1",
]


# ------------------------------------------------------------------------------
# Реестр роутеров v1
#   Элемент: (name, APIRouter, is_absolute)
#   is_absolute=False -> относительный префикс (например, '/auth'), монтируем с base_prefix.
#   is_absolute=True  -> абсолютный префикс (например, '/api/v1/campaigns'), монтируем как есть.
# ------------------------------------------------------------------------------
def _router_or_none(mod: Any) -> APIRouter | None:
    try:
        r = getattr(mod, "router", None)
        return r if isinstance(r, APIRouter) else None
    except Exception:
        return None


V1_ROUTERS: list[tuple[str, APIRouter, bool]] = []
if auth_mod and _router_or_none(auth_mod):
    V1_ROUTERS.append(("auth", auth_mod.router, False))
if users_mod and _router_or_none(users_mod):
    V1_ROUTERS.append(("users", users_mod.router, False))
if products_mod and _router_or_none(products_mod):
    V1_ROUTERS.append(("products", products_mod.router, False))
if campaigns_mod and _router_or_none(campaigns_mod):
    # в этом модуле prefix уже абсолютный '/api/v1/campaigns'
    V1_ROUTERS.append(("campaigns", campaigns_mod.router, True))
if analytics_mod and _router_or_none(analytics_mod):
    V1_ROUTERS.append(("analytics", analytics_mod.router, False))

# Поддержка кошелька и платежей, если модули присутствуют
if wallet and _router_or_none(wallet):
    # в wallet.py объявлен prefix '/api/v1/wallet'
    V1_ROUTERS.append(("wallet", wallet.router, True))
if payments and _router_or_none(payments):
    # ожидаемый prefix '/api/v1/payments'
    V1_ROUTERS.append(("payments", payments.router, True))
if invoices and _router_or_none(invoices):
    # ожидаемый prefix '/api/v1/invoices'
    V1_ROUTERS.append(("invoices", invoices.router, True))

# ⬇⬇⬇ ДОБАВЛЕНО: регистрация Kaspi
if kaspi_mod and _router_or_none(kaspi_mod):
    # в kaspi.py объявлен prefix '/api/v1/kaspi' → абсолютный
    V1_ROUTERS.append(("kaspi", kaspi_mod.router, True))
if integrations_mod and _router_or_none(integrations_mod):
    V1_ROUTERS.append(("integrations", integrations_mod.router, True))
if debug_db_mod and _router_or_none(debug_db_mod):
    V1_ROUTERS.append(("debug_db", debug_db_mod.router, True))


def register_v1_router(name: str, router: APIRouter, is_absolute: bool = False) -> None:
    """
    Явная регистрация/обновление v1-роутера (для будущих модулей).
    """
    for i, (n, _, _) in enumerate(V1_ROUTERS):
        if n == name:
            V1_ROUTERS[i] = (name, router, is_absolute)
            break
    else:
        V1_ROUTERS.append((name, router, is_absolute))


def register_optional_v1_router(name: str, import_path: str, is_absolute_hint: bool | None = None) -> bool:
    """
    Опциональная регистрация роутера по строке импорта.
    Возвращает True, если модуль зарегистрирован; False — если отсутствует.
    """
    mod = _try_import(import_path)
    r = _router_or_none(mod) if mod else None
    if not r:
        return False

    abs_flag = bool(is_absolute_hint) or _router_prefix_startswith(r, "/api/v1")
    register_v1_router(name, r, abs_flag)
    return True


# ------------------------------------------------------------------------------
# Утилиты определения префиксов и «умное» подключение
# ------------------------------------------------------------------------------
def _router_first_path(router: APIRouter) -> str | None:
    """Возвращает первый путь (route.path) у заданного роутера, если есть."""
    for r in getattr(router, "routes", []) or []:
        p = getattr(r, "path", None)
        if isinstance(p, str):
            return p
    return None


def _router_prefix(router: APIRouter) -> str:
    """Безопасно получает объявленный префикс роутера (router.prefix), иначе ''."""
    px = getattr(router, "prefix", "") or ""
    return px if isinstance(px, str) else ""


def _router_prefix_startswith(router: APIRouter, base_prefix: str) -> bool:
    """Проверяет, начинается ли prefix роутера с base_prefix (без учёта конечного /)."""
    base = base_prefix.rstrip("/") or "/"
    rp = _router_prefix(router).rstrip("/")
    if not rp:
        return False
    return rp == base or rp.startswith(base + "/")


def _looks_absolute_under_base(router: APIRouter, base_prefix: str) -> bool:
    """
    Эвристика абсолютности:
    1) по объявленному router.prefix;
    2) по первому зарегистрированному пути роутера.
    """
    if _router_prefix_startswith(router, base_prefix):
        return True
    p = _router_first_path(router) or ""
    base = base_prefix.rstrip("/")
    return p.startswith(base + "/") or p == base


Target = Union[FastAPI, APIRouter]


def _get_or_init_mounted_set(target: Target) -> set:
    """
    Ведём набор уже смонтированных APIRouter по id(router),
    чтобы не подключить один и тот же роутер дважды.

    FastAPI имеет .state — используем его;
    APIRouter может не иметь .state — храним атрибут на самом объекте.
    """
    # Попытка через .state
    state = getattr(target, "state", None)
    if state is not None:
        if not hasattr(state, "_mounted_router_ids"):
            state._mounted_router_ids = set()  # type: ignore[attr-defined]
        return state._mounted_router_ids  # type: ignore[attr-defined]

    # Фоллбэк: навешиваем атрибут на сам объект
    if not hasattr(target, "_mounted_router_ids"):
        setattr(target, "_mounted_router_ids", set())
    return getattr(target, "_mounted_router_ids")


def _mount_once(target: Target, router: APIRouter, base_prefix: str, is_absolute_flag: bool | None) -> str:
    """
    Подключает роутер один раз. Если уже подключали — возвращает пояснение и пропускает.
    """
    mounted = _get_or_init_mounted_set(target)
    rid = id(router)
    if rid in mounted:
        fp = _router_first_path(router) or (_router_prefix(router) or "<unknown>")
        note = f"Skipped duplicate include for router ({fp})"
        return note

    # Определяем абсолютность
    is_abs = bool(is_absolute_flag) or _looks_absolute_under_base(router, base_prefix)
    if is_abs:
        target.include_router(router)  # type: ignore[arg-type]
        fp = _router_first_path(router) or (_router_prefix(router) or "<unknown>")
        note = f"Included router as-is (absolute prefix): {fp}"
    else:
        target.include_router(router, prefix=base_prefix)  # type: ignore[arg-type]
        fp = _router_first_path(router) or "<root>"
        note = f"Included router with base prefix '{base_prefix}': {fp}"

    mounted.add(rid)
    return note


def include_router_smart(target: Target, router: APIRouter, base_prefix: str) -> str:
    """
    Подключает роутер «умно» (с автоопределением абсолютности).
    Возвращает строку-описание способа подключения.
    """
    return _mount_once(target, router, base_prefix, is_absolute_flag=None)


def _mount_v1_diagnostics(target: Target, base_prefix: str) -> None:
    """
    Монтирует диагностические эндпоинты под /api/v1:
      - /api/v1/_debug/routers
      - /api/v1/health-v1
    """
    diag_router = APIRouter(prefix=base_prefix, tags=["diagnostics"])

    @diag_router.get(
        "/_debug/routers",
        response_class=JSONResponse,
        summary="List loaded v1 routers",
        include_in_schema=False,
    )
    def _list_loaded_v1_routers():
        return {
            "registered": get_v1_registry(),
            "time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }

    @diag_router.get(
        "/health-v1",
        summary="API v1 health (routers registry)",
        include_in_schema=False,
    )
    def _health_v1():
        return {
            "status": "ok",
            "prefix": base_prefix,
            "registered": get_v1_registry(),
            "time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }

    # защитимся от двойного монтирования
    _mount_once(target, diag_router, base_prefix, is_absolute_flag=True)


def mount_v1(target: Target, base_prefix: str = "/api/v1") -> None:
    """
    Подключает все роутеры v1 из реестра. Надёжно определяет абсолютность и
    защищает от двойного подключения. Работает как с FastAPI, так и с APIRouter.
    Также монтирует диагностические эндпоинты под /api/v1.
    """
    current_settings = config.get_settings()
    env = str(getattr(current_settings, "ENVIRONMENT", "") or "").lower()
    debug_enabled = bool(current_settings.DEBUG) or env in {"development", "dev", "local"}

    for name, router, is_absolute in V1_ROUTERS:
        if name == "debug_db" and not debug_enabled:
            continue
        try:
            _mount_once(target, router, base_prefix, is_absolute_flag=is_absolute)
        except Exception as e:
            logger.exception("Failed to include router '%s': %s", name, e)

    # Диагностика (/_debug/routers и /health-v1)
    if debug_enabled:
        try:
            _mount_v1_diagnostics(target, base_prefix)
        except Exception as e:
            logger.debug("Mount v1 diagnostics skipped: %s", e)


def mount_all(target: Target, base_prefix: str = "/api/v1") -> None:
    """Синоним mount_v1 для единообразия в других местах проекта."""
    mount_v1(target, base_prefix=base_prefix)


def mount_into_router(parent: APIRouter, base_prefix: str = "/api/v1") -> None:
    """
    Удобство для случаев, когда нужно примонтировать v1 в другой APIRouter.
    Эквивалентно: mount_v1(parent, base_prefix).
    """
    mount_v1(parent, base_prefix=base_prefix)


# ------------------------------------------------------------------------------
# Диагностика и инспекция реестра
# ------------------------------------------------------------------------------
def get_v1_registry() -> list[dict[str, Any]]:
    """
    Возвращает срез по реестру: имя, абсолютность, объявленный префикс и первый путь.
    Удобно для health-страниц/логов/тестов.
    """
    out: list[dict[str, Any]] = []
    for name, router, is_abs in V1_ROUTERS:
        out.append(
            {
                "name": name,
                "is_absolute": is_abs,
                "declared_prefix": _router_prefix(router),
                "first_path": _router_first_path(router),
            }
        )
    return out


def get_mount_diagnostics() -> dict[str, Any]:
    return {
        "registered": get_v1_registry(),
        "counts": {"registered": len(V1_ROUTERS)},
        "module_timings_ms": dict(_IMPORT_TIMINGS_MS),
    }


def diagnose_v1(target: Target) -> dict[str, Any]:
    """
    Лёгкая диагностика подключения v1-роутеров.
    Возвращает список зарегистрированных, а также информацию о том, что уже примонтировано.
    """
    mounted = list(getattr(getattr(target, "state", target), "_mounted_router_ids", set()))
    return {
        "registered": get_v1_registry(),
        "mounted_count": len(mounted),
    }
