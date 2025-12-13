"""
Payment Pydantic schemas.
"""

from decimal import Decimal

from pydantic import Field

from app.models.payment import PaymentMethod, PaymentProvider, PaymentStatus
from app.schemas.base import BaseCreateSchema, BaseResponseSchema, BaseUpdateSchema


class PaymentCreate(BaseCreateSchema):
    """Schema for creating a payment"""

    order_id: int
    amount: Decimal = Field(..., gt=0, decimal_places=2)
    currency: str = "KZT"
    provider: PaymentProvider = PaymentProvider.TIPTOP
    method: PaymentMethod = PaymentMethod.CARD
    description: str | None = None
    idempotency_key: str | None = Field(None, max_length=128)


class PaymentUpdate(BaseUpdateSchema):
    """Schema for updating a payment"""

    status: PaymentStatus | None = None
    external_id: str | None = Field(None, max_length=128)
    provider_data: str | None = None
    receipt_url: str | None = Field(None, max_length=1024)
    receipt_number: str | None = Field(None, max_length=64)
    failure_reason: str | None = Field(None, max_length=255)
    failure_code: str | None = Field(None, max_length=32)


class PaymentResponse(BaseResponseSchema):
    """Schema for payment response"""

    payment_number: str
    external_id: str | None
    provider_invoice_id: str | None
    provider: PaymentProvider
    method: PaymentMethod
    status: PaymentStatus
    amount: Decimal
    fee_amount: Decimal
    refunded_amount: Decimal
    currency: str
    receipt_url: str | None
    receipt_number: str | None
    description: str | None
    processed_at: str | None
    confirmed_at: str | None
    failed_at: str | None
    failure_reason: str | None
    is_successful: bool
    is_refundable: bool
    available_refund_amount: float
    order_id: int


class PaymentRefundCreate(BaseCreateSchema):
    """Schema for creating a payment refund"""

    amount: Decimal = Field(..., gt=0, decimal_places=2)
    reason: str | None = Field(None, max_length=255)
    notes: str | None = None


class PaymentRefundResponse(BaseResponseSchema):
    """Schema for payment refund response"""

    refund_number: str
    external_id: str | None
    amount: Decimal
    currency: str
    status: PaymentStatus
    reason: str | None
    notes: str | None
    processed_at: str | None
    completed_at: str | None
    payment_id: int


class WebhookPayment(BaseCreateSchema):
    """Schema for payment webhook"""

    event_id: str
    provider_invoice_id: str
    status: str
    amount: Decimal
    currency: str = "KZT"
    external_id: str | None = None
    receipt_url: str | None = None
    failure_reason: str | None = None
    metadata: dict | None = None


class PaymentIntentCreate(BaseCreateSchema):
    """Schema for creating payment intent"""

    order_id: int
    amount: Decimal = Field(..., gt=0, decimal_places=2)
    currency: str = "KZT"
    return_url: str | None = None
    webhook_url: str | None = None
    description: str | None = None


class PaymentIntentResponse(BaseCreateSchema):
    """Schema for payment intent response"""

    payment_id: int
    payment_url: str
    qr_code_url: str | None = None
    expires_at: str
