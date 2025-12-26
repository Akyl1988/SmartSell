from __future__ import annotations

from typing import Any

from app.integrations.ports.messaging import MessagingProvider


class NoOpMessagingProvider(MessagingProvider):
    async def send_message(
        self,
        to: str,
        text: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {"status": "noop", "to": to, "text": text, "metadata": metadata or {}}


__all__ = ["NoOpMessagingProvider"]
