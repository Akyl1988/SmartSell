"""
Payment Pydantic schemas.
"""

from decimal import Decimal
from typing import Optional

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
    description: Optional[str] = None
    idempotency_key: Optional[str] = Field(None, max_length=128)


class PaymentUpdate(BaseUpdateSchema):
    """Schema for updating a payment"""

    status: Optional[PaymentStatus] = None
    external_id: Optional[str] = Field(None, max_length=128)
    provider_data: Optional[str] = None
    receipt_url: Optional[str] = Field(None, max_length=1024)
    receipt_number: Optional[str] = Field(None, max_length=64)
    failure_reason: Optional[str] = Field(None, max_length=255)
    failure_code: Optional[str] = Field(None, max_length=32)


class PaymentResponse(BaseResponseSchema):
    """Schema for payment response"""

    payment_number: str
    external_id: Optional[str]
    provider_invoice_id: Optional[str]
    provider: PaymentProvider
    method: PaymentMethod
    status: PaymentStatus
    amount: Decimal
    fee_amount: Decimal
    refunded_amount: Decimal
    currency: str
    receipt_url: Optional[str]
    receipt_number: Optional[str]
    description: Optional[str]
    processed_at: Optional[str]
    confirmed_at: Optional[str]
    failed_at: Optional[str]
    failure_reason: Optional[str]
    is_successful: bool
    is_refundable: bool
    available_refund_amount: float
    order_id: int


class PaymentRefundCreate(BaseCreateSchema):
    """Schema for creating a payment refund"""

    amount: Decimal = Field(..., gt=0, decimal_places=2)
    reason: Optional[str] = Field(None, max_length=255)
    notes: Optional[str] = None


class PaymentRefundResponse(BaseResponseSchema):
    """Schema for payment refund response"""

    refund_number: str
    external_id: Optional[str]
    amount: Decimal
    currency: str
    status: PaymentStatus
    reason: Optional[str]
    notes: Optional[str]
    processed_at: Optional[str]
    completed_at: Optional[str]
    payment_id: int


class WebhookPayment(BaseCreateSchema):
    """Schema for payment webhook"""

    event_id: str
    provider_invoice_id: str
    status: str
    amount: Decimal
    currency: str = "KZT"
    external_id: Optional[str] = None
    receipt_url: Optional[str] = None
    failure_reason: Optional[str] = None
    metadata: Optional[dict] = None


class PaymentIntentCreate(BaseCreateSchema):
    """Schema for creating payment intent"""

    order_id: int
    amount: Decimal = Field(..., gt=0, decimal_places=2)
    currency: str = "KZT"
    return_url: Optional[str] = None
    webhook_url: Optional[str] = None
    description: Optional[str] = None


class PaymentIntentResponse(BaseCreateSchema):
    """Schema for payment intent response"""

    payment_id: int
    payment_url: str
    qr_code_url: Optional[str] = None
    expires_at: str
