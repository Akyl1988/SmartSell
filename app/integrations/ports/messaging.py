from __future__ import annotations

from typing import Any, Protocol


class MessagingProvider(Protocol):
    async def send_message(
        self,
        to: str,
        text: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        ...


__all__ = ["MessagingProvider"]
