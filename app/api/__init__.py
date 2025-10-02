from __future__ import annotations

"""
Единая точка реэкспорта API и обратной совместимости.

Современный путь:
    from app.api.routes import mount_v1
    mount_v1(app, base_prefix="/api/v1")

Обратная совместимость:
    from app.api import api_router
    app.include_router(api_router)

Особенности:
- Опциональные модули (wallet, payments) могут отсутствовать — не валим приложение.
- «Умное» включение: если router.prefix уже абсолютный (/api/v1/...), подключаем как есть;
  иначе — монтируем под /v1.
"""

import logging
from typing import Any, Optional

from fastapi import APIRouter

# Рекомендуемый современный агрегатор (экспортируем наружу)
from app.api.routes import (
    mount_v1,             # монтирование в FastAPI/APIRouter
    mount_all,            # alias на mount_v1
    include_router_smart, # умное подключение одного роутера
)

logger = logging.getLogger(__name__)
if not logger.handlers:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s [%(name)s] %(message)s")


# ---------------------------------------------------------------------------
# Безопасные импорты базовых v1-модулей
# ---------------------------------------------------------------------------
def _try_import(path: str) -> Optional[Any]:
    try:
        module = __import__(path, fromlist=["router"])
        return module
    except Exception as e:
        logger.debug("optional import failed: %s (%s)", path, e)
        return None


_auth_mod      = _try_import("app.api.v1.auth")
_users_mod     = _try_import("app.api.v1.users")
_products_mod  = _try_import("app.api.v1.products")
_campaigns_mod = _try_import("app.api.v1.campaigns")
_wallet_mod    = _try_import("app.api.v1.wallet")
_payments_mod  = _try_import("app.api.v1.payments")

# Для прямого реэкспорта модулей (если кто-то делает `from app.api import wallet`)
auth      = _auth_mod
users     = _users_mod
products  = _products_mod
campaigns = _campaigns_mod
wallet    = _wallet_mod   # может быть None
payments  = _payments_mod # может быть None


# ---------------------------------------------------------------------------
# Вспомогательные функции для безопасного подключения роутеров
# ---------------------------------------------------------------------------
def _get_router(mod: Any) -> Optional[APIRouter]:
    """Аккуратно достаёт APIRouter из модуля (если есть)."""
    try:
        if mod is None:
            return None
        r = getattr(mod, "router", None)
        return r if isinstance(r, APIRouter) else None
    except Exception:
        return None


def _is_absolute_prefix(router: APIRouter) -> bool:
    """Проверяет, является ли prefix абсолютным (/api/...)."""
    try:
        px = (router.prefix or "").strip()
        return bool(px) and px.startswith("/api/")
    except Exception:
        return False


def _safe_include(parent: APIRouter, mod: Any, fallback_prefix: str = "/v1") -> bool:
    """
    Вставляет дочерний router в parent безопасно:
    - если у дочернего router абсолютный префикс (/api/..), подключаем как есть;
    - иначе — вешаем под fallback_prefix (по умолчанию /v1);
    Возвращает True, если роутер подключили.
    """
    r = _get_router(mod)
    if not r:
        return False
    try:
        if _is_absolute_prefix(r):
            parent.include_router(r)
            logger.debug("Included router as-is: %s", r.prefix)
        else:
            parent.include_router(r, prefix=fallback_prefix)
            logger.debug("Included router with prefix %s: %s", fallback_prefix, r.prefix or "<root>")
        return True
    except Exception as e:
        logger.warning("Router include failed (%s): %s", getattr(mod, "__name__", mod), e)
        return False


# ---------------------------------------------------------------------------
# Обратная совместимость: единый api_router
#   Старый код мог делать: from app.api import api_router; app.include_router(api_router)
#   Мы собираем его из доступных модулей, не валимся при отсутствии wallet/payments.
# ---------------------------------------------------------------------------
api_router = APIRouter()

_safe_include(api_router, _auth_mod,      fallback_prefix="/v1")
_safe_include(api_router, _users_mod,     fallback_prefix="/v1")
_safe_include(api_router, _products_mod,  fallback_prefix="/v1")
_safe_include(api_router, _campaigns_mod, fallback_prefix="/v1")

if not _safe_include(api_router, _wallet_mod, fallback_prefix="/v1"):
    logger.info("Wallet API module not present — skipping")
if not _safe_include(api_router, _payments_mod, fallback_prefix="/v1"):
    logger.info("Payments API module not present — skipping")


# Функция-обёртка: получить актуально собранный роутер v1 (полезно для тестов/утилит)
def get_api_router() -> APIRouter:
    return api_router


__all__ = [
    # современный путь
    "mount_v1", "mount_all", "include_router_smart",
    # базовые/опциональные модули
    "auth", "users", "products", "campaigns", "wallet", "payments",
    # обратная совместимость
    "api_router", "get_api_router",
]
