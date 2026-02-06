from __future__ import annotations

from typing import Any, Protocol


class MediaProvider(Protocol):
    async def upload(
        self,
        file: bytes | str,
        *,
        public_id: str | None = None,
        folder: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        ...

    async def remove(self, public_id: str) -> dict[str, Any]:
        ...


__all__ = ["MediaProvider"]
