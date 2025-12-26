from __future__ import annotations

from typing import Any

from app.integrations.ports.otp import OtpProvider
from app.core.logging import get_logger

log = get_logger(__name__)


class MobizonOtpProvider(OtpProvider):
    def __init__(self, *, config: dict[str, Any] | None = None, name: str | None = None, version: int | None = None):
        self.name = (name or "mobizon").strip() or "mobizon"
        self.config = config or {}
        self.version = int(version or 0)

        required = ("api_key",)
        missing = [k for k in required if not self.config.get(k)]
        if missing:
            raise ValueError(f"mobizon otp config missing keys: {', '.join(missing)}")

    async def send_otp(
        self,
        phone: str,
        code: str,
        ttl_seconds: int,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload = {
            "status": "stub",
            "provider": self.name,
            "version": self.version,
            "to": phone,
            "code": code,
            "ttl_seconds": ttl_seconds,
            "metadata": metadata or {},
            "config": self.config,
        }
        log.info("MobizonOtpProvider stub send", extra={"phone": phone, "provider": self.name})
        return payload


__all__ = ["MobizonOtpProvider"]
