from __future__ import annotations

"""
Kaspi.kz integration: product feed generation, orders sync, and availability sync.

This module is the public facade that composes extracted mixins:
- HTTP transport/API operations
- feed generation and availability sync
- orders sync/state/lock logic

Public contracts remain here for compatibility.
"""

from typing import Optional

import anyio
import httpx

from app.core.config import settings
from app.core.logging import get_logger
from app.services.kaspi_service_feed import KaspiServiceFeedMixin
from app.services.kaspi_service_http import KaspiServiceHttpMixin
from app.services.kaspi_service_sync import (
    KaspiBadRequestError,
    KaspiProductsUpstreamError,
    KaspiServiceSyncMixin,
    KaspiSyncAlreadyRunning,
)
from app.services.kaspi_service_transport import _safe_httpx_request, _safe_httpx_response
from app.services.kaspi_service_utils import DEFAULT_KASPI_ORDER_STATES, _normalize_kaspi_base_url

logger = get_logger(__name__)


class KaspiService(KaspiServiceSyncMixin, KaspiServiceHttpMixin, KaspiServiceFeedMixin):
    """
    Базовые операции интеграции с Kaspi.kz:
      - загрузка заказов/деталей
      - обновление статуса заказа
      - загрузка товаров
      - генерация XML-фида
      - синхронизация заказов в локальную БД
      - (опционально) массовый апдейт доступности
    """

    def __init__(self, api_key: Optional[str] = None, base_url: Optional[str] = None):
        self.api_key = api_key or getattr(settings, "KASPI_API_TOKEN", "") or ""
        raw_base_url = (base_url or getattr(settings, "KASPI_API_URL", "") or "").rstrip("/")
        self.base_url = _normalize_kaspi_base_url(raw_base_url)
        self.headers = {
            "Authorization": f"Bearer {self.api_key}" if self.api_key else "",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

        if not hasattr(self, "_sync_timeout_seconds"):
            self._sync_timeout_seconds = getattr(settings, "KASPI_SYNC_TIMEOUT_SECONDS", 30)

        if not self.base_url:
            logger.warning("KaspiService: BASE URL не задан (settings.KASPI_API_URL).")
        if not self.api_key:
            logger.warning("KaspiService: API ключ не задан (settings.KASPI_API_TOKEN).")


__all__ = [
    "KaspiService",
    "KaspiSyncAlreadyRunning",
    "KaspiBadRequestError",
    "KaspiProductsUpstreamError",
    "DEFAULT_KASPI_ORDER_STATES",
    "settings",
    "httpx",
    "anyio",
    "_safe_httpx_request",
    "_safe_httpx_response",
]
