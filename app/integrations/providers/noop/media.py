from __future__ import annotations

from typing import Any

from app.core.config import settings
from app.core.logging import get_logger
from app.integrations.errors import ProviderNotConfiguredError
from app.integrations.ports.media import MediaProvider

log = get_logger(__name__)


class NoOpMediaProvider(MediaProvider):
    def __init__(
        self,
        name: str | None = None,
        config: dict[str, Any] | None = None,
        version: int | None = None,
    ):
        self.name = (name or "noop").strip() or "noop"
        self.config = config or {}
        self.version = int(version or 0)

    async def upload(
        self,
        file: bytes | str,
        *,
        public_id: str | None = None,
        folder: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if settings.is_production:
            raise ProviderNotConfiguredError("media_provider_not_configured")
        log.warning("Using noop media provider (non-production)")
        return {
            "status": "noop",
            "provider": self.name,
            "version": self.version,
        }

    async def remove(self, public_id: str) -> dict[str, Any]:
        if settings.is_production:
            raise ProviderNotConfiguredError("media_provider_not_configured")
        log.warning("Using noop media provider (non-production)")
        return {
            "status": "noop",
            "provider": self.name,
            "version": self.version,
        }


__all__ = ["NoOpMediaProvider"]
