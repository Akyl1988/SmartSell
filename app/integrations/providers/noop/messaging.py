from __future__ import annotations

from typing import Any

from app.integrations.ports.messaging import MessagingProvider


class NoOpMessagingProvider(MessagingProvider):
    def __init__(
        self,
        name: str | None = None,
        config: dict[str, Any] | None = None,
        version: int | None = None,
    ):
        self.name = (name or "noop").strip() or "noop"
        self.config = config or {}
        self.version = int(version or 0)

    async def send_message(
        self,
        to: str,
        text: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {
            "status": "noop",
            "provider": self.name,
            "version": self.version,
            "to": to,
            "text": text,
            "metadata": metadata or {},
            "config": self.config,
        }


__all__ = ["NoOpMessagingProvider"]
