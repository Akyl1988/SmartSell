from __future__ import annotations

import hashlib
from decimal import Decimal
from typing import Any
from uuid import uuid4

from app.core.config import settings
from app.core.logging import get_logger
from app.integrations.errors import ProviderNotConfiguredError
from app.integrations.ports.payments import PaymentGateway, PaymentIntent
from app.services.tiptop_service import TipTopService

log = get_logger(__name__)


class TipTopPaymentGateway(PaymentGateway):
    """Minimal TipTop Pay adapter with idempotent intent creation."""

    def __init__(
        self,
        name: str | None = None,
        config: dict[str, Any] | None = None,
        version: int | None = None,
    ):
        self.name = (name or "tiptop").strip() or "tiptop"
        self.config = config or {}
        self.version = int(version or 0)

    @property
    def provider_name(self) -> str:
        return self.name

    @property
    def provider_version(self) -> int:
        return self.version

    def _resolve_config(self) -> tuple[str, str, str]:
        api_key = (self.config.get("api_key") or settings.TIPTOP_API_KEY or "").strip()
        api_secret = (self.config.get("api_secret") or settings.TIPTOP_API_SECRET or "").strip()
        api_url = (self.config.get("api_url") or settings.TIPTOP_API_URL or "").strip()
        if not api_key or not api_secret or not api_url:
            raise ProviderNotConfiguredError("payment_provider_not_configured")
        return api_key, api_secret, api_url

    def _make_service(self) -> TipTopService:
        api_key, api_secret, api_url = self._resolve_config()
        service = TipTopService()
        service.api_key = api_key
        service.api_secret = api_secret
        service.base_url = api_url
        service.headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        return service

    async def healthcheck(self) -> dict[str, Any]:
        self._resolve_config()
        return {
            "status": "ok",
            "provider": self.name,
            "version": self.version,
        }

    async def create_payment_intent(
        self,
        amount: Decimal,
        currency: str,
        customer_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> PaymentIntent:
        meta = metadata or {}
        service = self._make_service()

        idempotency_key = meta.get("idempotency_key")
        if not idempotency_key:
            base = f"{customer_id}:{amount}:{(currency or '').upper()}"
            idempotency_key = hashlib.sha256(base.encode("utf-8")).hexdigest()

        payload_order_id = meta.get("order_id") or customer_id
        payload_description = meta.get("description") or "Payment"

        try:
            data = await service.create_payment(
                amount=float(amount),
                currency=(currency or "").upper(),
                order_id=str(payload_order_id),
                description=str(payload_description),
                return_url=meta.get("return_url"),
                webhook_url=meta.get("webhook_url"),
                idempotency_key=idempotency_key,
            )
        except Exception as exc:  # pragma: no cover - external API guard
            log.warning("TipTop payment intent failed", exc_info=exc)
            raise ProviderNotConfiguredError("payment_provider_unavailable")

        provider_intent_id = (
            str(data.get("payment_id") or data.get("id") or data.get("invoice_id") or data.get("transaction_id"))
            if isinstance(data, dict)
            else ""
        )
        if not provider_intent_id:
            provider_intent_id = uuid4().hex

        status = "created"
        if isinstance(data, dict) and data.get("status"):
            status = str(data.get("status"))

        return PaymentIntent(
            id=uuid4().hex,
            status=status,
            amount=Decimal(str(amount)),
            currency=(currency or "").upper(),
            customer_id=str(customer_id),
            provider=self.name,
            provider_intent_id=provider_intent_id,
            provider_version=self.version,
            metadata={**meta, "provider_response": data} if isinstance(data, dict) else meta,
        )

    async def refund(
        self,
        transaction_id: str,
        amount: float | None = None,
        reason: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        service = self._make_service()
        try:
            data = await service.create_refund(
                payment_id=str(transaction_id),
                amount=float(amount) if amount is not None else 0.0,
                reason=reason or "Refund",
            )
        except Exception as exc:  # pragma: no cover - external API guard
            log.warning("TipTop refund failed", exc_info=exc)
            raise ProviderNotConfiguredError("payment_provider_unavailable")
        return {
            "status": data.get("status", "refunded"),
            "provider": self.name,
            "version": self.version,
            "transaction_id": transaction_id,
            "refunded": True,
            "amount": amount,
            "reason": reason,
            "metadata": metadata or {},
            "provider_response": data,
        }

    async def charge(
        self,
        amount: Decimal,
        currency: str,
        customer_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        intent = await self.create_payment_intent(amount, currency, customer_id, metadata)
        return {
            "status": intent.status,
            "provider": intent.provider,
            "provider_intent_id": intent.provider_intent_id,
            "amount": str(intent.amount),
            "currency": intent.currency,
            "customer_id": intent.customer_id,
        }


__all__ = ["TipTopPaymentGateway"]
