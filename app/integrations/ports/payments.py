from __future__ import annotations

from typing import Any, Protocol


class PaymentGateway(Protocol):
    async def healthcheck(self) -> dict[str, Any]:
        ...

    async def create_payment_intent(
        self,
        amount: float,
        currency: str,
        customer_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        ...

    async def refund(
        self,
        transaction_id: str,
        amount: float | None = None,
        reason: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        ...

    # Backward-compat alias for older call sites
    async def charge(
        self,
        amount: float,
        currency: str,
        customer_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        ...

    @property
    def provider_name(self) -> str:  # noqa: D401 - simple accessors
        ...

    @property
    def provider_version(self) -> int:
        ...


__all__ = ["PaymentGateway"]
