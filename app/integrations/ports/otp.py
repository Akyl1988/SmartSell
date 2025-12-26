from __future__ import annotations

from typing import Any, Protocol


class OtpProvider(Protocol):
    async def send_otp(
        self,
        phone: str,
        code: str,
        ttl_seconds: int,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        ...

    async def verify_otp(
        self,
        phone: str,
        code: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        ...


__all__ = ["OtpProvider"]
