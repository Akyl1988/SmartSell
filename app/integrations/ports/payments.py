from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Protocol


@dataclass(frozen=True)
class PaymentIntent:
    id: str
    status: str
    amount: Decimal
    currency: str
    customer_id: str
    provider: str
    provider_intent_id: str
    provider_version: int
    metadata: dict[str, Any]


class PaymentGateway(Protocol):
    async def healthcheck(self) -> dict[str, Any]:
        ...

    async def create_payment_intent(
        self,
        amount: Decimal,
        currency: str,
        customer_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> PaymentIntent:
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
        amount: Decimal,
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


__all__ = ["PaymentGateway", "PaymentIntent"]
