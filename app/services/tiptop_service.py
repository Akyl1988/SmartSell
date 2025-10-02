"""
TipTop Pay payment service integration.
"""

import hashlib
import hmac
from datetime import datetime
from typing import Any

import httpx

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


class TipTopService:
    """Service for TipTop Pay integration"""

    def __init__(self):
        self.api_key = settings.TIPTOP_API_KEY
        self.api_secret = settings.TIPTOP_API_SECRET
        self.base_url = settings.TIPTOP_API_URL
        self.headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    async def create_payment(
        self,
        amount: float,
        currency: str = "KZT",
        order_id: str = None,
        description: str = None,
        return_url: str = None,
        webhook_url: str = None,
        idempotency_key: str = None,
    ) -> dict[str, Any]:
        """Create payment with TipTop Pay"""

        payload = {
            "amount": amount,
            "currency": currency,
            "description": description or "Payment",
            "order_id": order_id,
            "return_url": return_url,
            "webhook_url": webhook_url,
        }

        if idempotency_key:
            payload["idempotency_key"] = idempotency_key

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    f"{self.base_url}/payments", headers=self.headers, json=payload
                )
                response.raise_for_status()

                data = response.json()
                logger.info(f"TipTop payment created: {data.get('payment_id')}")
                return data

        except httpx.HTTPError as e:
            logger.error(f"TipTop create payment error: {e}")
            if hasattr(e, "response") and e.response:
                logger.error(f"Response: {e.response.text}")
            raise Exception(f"Failed to create TipTop payment: {e}")

    async def get_payment_status(self, payment_id: str) -> dict[str, Any]:
        """Get payment status from TipTop Pay"""

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(
                    f"{self.base_url}/payments/{payment_id}", headers=self.headers
                )
                response.raise_for_status()

                data = response.json()
                logger.info(f"TipTop payment {payment_id} status: {data.get('status')}")
                return data

        except httpx.HTTPError as e:
            logger.error(f"TipTop get payment status error: {e}")
            raise Exception(f"Failed to get payment status: {e}")

    async def create_refund(
        self, payment_id: str, amount: float, reason: str = None
    ) -> dict[str, Any]:
        """Create refund for payment"""

        payload = {
            "payment_id": payment_id,
            "amount": amount,
            "reason": reason or "Refund",
        }

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    f"{self.base_url}/refunds", headers=self.headers, json=payload
                )
                response.raise_for_status()

                data = response.json()
                logger.info(f"TipTop refund created: {data.get('refund_id')}")
                return data

        except httpx.HTTPError as e:
            logger.error(f"TipTop create refund error: {e}")
            raise Exception(f"Failed to create refund: {e}")

    async def get_refund_status(self, refund_id: str) -> dict[str, Any]:
        """Get refund status"""

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(
                    f"{self.base_url}/refunds/{refund_id}", headers=self.headers
                )
                response.raise_for_status()

                data = response.json()
                logger.info(f"TipTop refund {refund_id} status: {data.get('status')}")
                return data

        except httpx.HTTPError as e:
            logger.error(f"TipTop get refund status error: {e}")
            raise Exception(f"Failed to get refund status: {e}")

    def verify_webhook_signature(self, payload: bytes, signature: str) -> bool:
        """Verify webhook signature"""

        try:
            # Calculate expected signature
            expected_signature = hmac.new(
                self.api_secret.encode("utf-8"), payload, hashlib.sha256
            ).hexdigest()

            # Compare signatures
            return hmac.compare_digest(signature, expected_signature)

        except Exception as e:
            logger.error(f"Webhook signature verification error: {e}")
            return False

    async def create_subscription(
        self,
        amount: float,
        currency: str = "KZT",
        interval: str = "month",
        interval_count: int = 1,
        customer_id: str = None,
        description: str = None,
    ) -> dict[str, Any]:
        """Create subscription for recurring payments"""

        payload = {
            "amount": amount,
            "currency": currency,
            "interval": interval,
            "interval_count": interval_count,
            "customer_id": customer_id,
            "description": description or "Subscription",
        }

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    f"{self.base_url}/subscriptions", headers=self.headers, json=payload
                )
                response.raise_for_status()

                data = response.json()
                logger.info(f"TipTop subscription created: {data.get('subscription_id')}")
                return data

        except httpx.HTTPError as e:
            logger.error(f"TipTop create subscription error: {e}")
            raise Exception(f"Failed to create subscription: {e}")

    async def cancel_subscription(self, subscription_id: str) -> bool:
        """Cancel subscription"""

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.delete(
                    f"{self.base_url}/subscriptions/{subscription_id}",
                    headers=self.headers,
                )
                response.raise_for_status()

                logger.info(f"TipTop subscription {subscription_id} cancelled")
                return True

        except httpx.HTTPError as e:
            logger.error(f"TipTop cancel subscription error: {e}")
            return False

    async def create_invoice(
        self,
        amount: float,
        currency: str = "KZT",
        description: str = None,
        due_date: datetime = None,
        customer_email: str = None,
    ) -> dict[str, Any]:
        """Create invoice"""

        payload = {
            "amount": amount,
            "currency": currency,
            "description": description or "Invoice",
            "customer_email": customer_email,
        }

        if due_date:
            payload["due_date"] = due_date.isoformat()

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    f"{self.base_url}/invoices", headers=self.headers, json=payload
                )
                response.raise_for_status()

                data = response.json()
                logger.info(f"TipTop invoice created: {data.get('invoice_id')}")
                return data

        except httpx.HTTPError as e:
            logger.error(f"TipTop create invoice error: {e}")
            raise Exception(f"Failed to create invoice: {e}")

    async def get_payment_methods(self) -> dict[str, Any]:
        """Get available payment methods"""

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(
                    f"{self.base_url}/payment-methods", headers=self.headers
                )
                response.raise_for_status()

                data = response.json()
                logger.info("Retrieved TipTop payment methods")
                return data

        except httpx.HTTPError as e:
            logger.error(f"TipTop get payment methods error: {e}")
            raise Exception(f"Failed to get payment methods: {e}")

    async def create_customer(
        self, phone: str, email: str = None, name: str = None
    ) -> dict[str, Any]:
        """Create customer in TipTop Pay"""

        payload = {"phone": phone, "email": email, "name": name}

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    f"{self.base_url}/customers", headers=self.headers, json=payload
                )
                response.raise_for_status()

                data = response.json()
                logger.info(f"TipTop customer created: {data.get('customer_id')}")
                return data

        except httpx.HTTPError as e:
            logger.error(f"TipTop create customer error: {e}")
            raise Exception(f"Failed to create customer: {e}")

    def generate_qr_code_url(self, payment_id: str) -> str:
        """Generate QR code URL for payment"""
        return f"{self.base_url}/payments/{payment_id}/qr"

    def generate_payment_url(self, payment_id: str) -> str:
        """Generate payment page URL"""
        return f"{self.base_url}/pay/{payment_id}"
