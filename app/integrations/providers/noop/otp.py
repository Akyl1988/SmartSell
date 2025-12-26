from __future__ import annotations

from typing import Any

from app.integrations.ports.otp import OtpProvider


class NoOpOtpProvider(OtpProvider):
    def __init__(self, name: str | None = None, config: dict[str, Any] | None = None, version: int | None = None):
        self.name = (name or "noop").strip() or "noop"
        self.config = config or {}
        self.version = int(version or 0)

    async def send_otp(
        self,
        phone: str,
        code: str,
        ttl_seconds: int,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {
            "status": "noop",
            "provider": self.name,
            "version": self.version,
            "to": phone,
            "code": code,
            "ttl_seconds": ttl_seconds,
            "metadata": metadata or {},
            "config": self.config,
        }

    async def verify_otp(
        self,
        phone: str,
        code: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {
            "status": "noop",
            "provider": self.name,
            "version": self.version,
            "to": phone,
            "code": code,
            "metadata": metadata or {},
            "config": self.config,
            "verified": True,
        }


__all__ = ["NoOpOtpProvider"]
