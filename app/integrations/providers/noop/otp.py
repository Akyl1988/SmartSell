from __future__ import annotations

from typing import Any

from app.integrations.ports.otp import OtpProvider


class NoOpOtpProvider(OtpProvider):
    async def send_otp(
        self,
        phone: str,
        code: str,
        ttl_seconds: int,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {
            "status": "noop",
            "to": phone,
            "code": code,
            "ttl_seconds": ttl_seconds,
            "metadata": metadata or {},
        }


__all__ = ["NoOpOtpProvider"]
