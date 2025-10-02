# app/routers/payments.py
from __future__ import annotations
"""
Payments router for payment processing and management (enterprise-grade).

Особенности:
- Company scoping + корректные soft-delete фильтры (is_deleted.is_(False)).
- Идемпотентность для create/refund (через зависимость ensure_idempotency).
- Вебхук TipTop: проверка подписи + идемпотентность по event_id.
- Единый стиль ошибок: bad_request / not_found / conflict / server_error.
- Аудит значимых действий.
"""

import json
import uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy import and_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

# DB-сессия
try:
    # если вы уже объединили ядро в app/core/database.py
    from app.core.database import get_db  # type: ignore
except Exception:
    from app.core.db import get_db  # fallback

# Депсы/безопасность/логи/ошибки/идемпотентность
from app.core.deps import api_rate_limit_dep, ensure_idempotency, set_idempotency_result
from app.core.security import get_current_user, require_manager
from app.core.errors import bad_request, not_found, conflict, server_error
from app.core.logging import audit_logger

# Модели/схемы
from app.models import Order, Payment, PaymentRefund, User
from app.schemas import (
    PaymentIntentCreate,
    PaymentIntentResponse,
    PaymentRefundCreate,
    PaymentResponse,
    WebhookPayment,
)

# Сервисы/утилиты
from app.services.tiptop_service import TipTopService
from app.utils.idempotency import ensure_webhook_idempotency

router = APIRouter(
    prefix="/payments",
    tags=["payments"],
    dependencies=[Depends(api_rate_limit_dep)],
)

# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------

def _order_company_scope(company_id: int):
    """Фильтр заказов компании с учётом soft-delete."""
    # Order.is_deleted может быть колонкой Boolean, используем is_(False) вместо `not ...`
    return and_(Order.company_id == company_id, Order.is_deleted.is_(False))

def _to_json_string(data: object) -> str:
    """Надёжно сериализуем произвольный payload в строку (если колонка у вас не JSONB)."""
    try:
        if isinstance(data, (dict, list, tuple)):
            return json.dumps(data, ensure_ascii=False)
        return str(data)
    except Exception:
        return str(data)

# ---------------------------------------------------------------------
# GET /payments
# ---------------------------------------------------------------------

@router.get("/", response_model=list[PaymentResponse], summary="Список платежей компании")
async def get_payments(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    stmt = (
        select(Payment)
        .join(Order, Payment.order_id == Order.id)
        .where(_order_company_scope(current_user.company_id))
        .order_by(Payment.created_at.desc(), Payment.id.desc())
    )
    payments = (await db.execute(stmt)).scalars().all()
    return payments

# ---------------------------------------------------------------------
# POST /payments/create
# ---------------------------------------------------------------------

@router.post(
    "/create",
    response_model=PaymentIntentResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Создать платёжный интент (TipTop)",
    dependencies=[Depends(require_manager), Depends(ensure_idempotency)],
)
async def create_payment_intent(
    payment_data: PaymentIntentCreate,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # 1) Заказ компании (не удалён)
    order = (
        await db.execute(
            select(Order).where(
                and_(
                    Order.id == payment_data.order_id,
                    _order_company_scope(current_user.company_id),
                )
            )
        )
    ).scalar_one_or_none()
    if not order:
        raise not_found("Order not found")

    # (опц.) Не даём платить уже оплаченное
    if getattr(order, "status", "").lower() == "paid":
        raise conflict("Order already paid")

    # 2) Создаём локальную запись Payment
    payment_number = f"PAY-{uuid.uuid4().hex[:8].upper()}"
    provider_idempotency_key = f"order-{order.id}-{uuid.uuid4().hex[:8]}"

    payment = Payment(
        order_id=order.id,
        payment_number=payment_number,
        provider_invoice_id=provider_idempotency_key,
        amount=payment_data.amount,
        currency=payment_data.currency,
        description=payment_data.description or f"Payment for order {getattr(order, 'order_number', order.id)}",
        status="created",
    )

    db.add(payment)
    try:
        await db.commit()
        await db.refresh(payment)
    except IntegrityError as e:
        await db.rollback()
        raise conflict("Payment create violates a constraint") from e

    # 3) Создаём платёж у провайдера
    try:
        tiptop = TipTopService()
        intent = await tiptop.create_payment(
            amount=float(payment_data.amount),
            currency=payment_data.currency,
            order_id=str(order.id),
            description=payment.description,
            return_url=payment_data.return_url,
            webhook_url=payment_data.webhook_url or "/api/webhooks/tiptop",
            idempotency_key=provider_idempotency_key,
        )

        # 4) Сохраняем реквизиты провайдера
        payment.external_id = intent.get("payment_id")
        payment.provider_data = _to_json_string(intent)
        payment.status = "processing"
        await db.commit()

        # 5) Аудит
        audit_logger.log_data_change(
            user_id=current_user.id,
            action="payment_create",
            resource_type="payment",
            resource_id=str(payment.id),
            changes={"amount": float(payment.amount), "order_id": order.id},
        )

        # 6) Фиксируем результат для идемпотентности (если используете хранение результата)
        if hasattr(request.state, "idempotency_key"):
            await set_idempotency_result(request.state.idempotency_key, status.HTTP_201_CREATED)

        return PaymentIntentResponse(
            payment_id=payment.id,
            payment_url=intent.get("payment_url"),
            qr_code_url=intent.get("qr_code_url"),
            expires_at=intent.get("expires_at"),
        )

    except Exception as e:
        payment.status = "failed"
        payment.failure_reason = str(e)
        await db.commit()
        raise server_error(f"Failed to create payment: {e!s}")

# ---------------------------------------------------------------------
# GET /payments/{payment_id}
# ---------------------------------------------------------------------

@router.get("/{payment_id}", response_model=PaymentResponse, summary="Получить платёж по ID")
async def get_payment(
    payment_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    stmt = (
        select(Payment)
        .join(Order, Payment.order_id == Order.id)
        .where(and_(Payment.id == payment_id, _order_company_scope(current_user.company_id)))
    )
    payment = (await db.execute(stmt)).scalar_one_or_none()
    if not payment:
        raise not_found("Payment not found")
    return payment

# ---------------------------------------------------------------------
# POST /payments/{payment_id}/refund
# ---------------------------------------------------------------------

@router.post(
    "/{payment_id}/refund",
    summary="Создать возврат платежа",
    dependencies=[Depends(require_manager), Depends(ensure_idempotency)],
)
async def create_refund(
    payment_id: int,
    refund_data: PaymentRefundCreate,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # 1) Ищем успешный платёж компании
    payment = (
        await db.execute(
            select(Payment)
            .join(Order, Payment.order_id == Order.id)
            .where(
                and_(
                    Payment.id == payment_id,
                    _order_company_scope(current_user.company_id),
                    Payment.status == "success",
                )
            )
        )
    ).scalar_one_or_none()
    if not payment:
        raise not_found("Payment not found or not refundable")

    # 2) Проверяем сумму
    available = float(getattr(payment, "available_refund_amount", 0.0))
    if float(refund_data.amount) > available:
        raise bad_request("Refund amount exceeds available amount")

    # 3) Локальная запись возврата
    refund_number = f"REF-{uuid.uuid4().hex[:8].upper()}"
    refund = PaymentRefund(
        payment_id=payment.id,
        refund_number=refund_number,
        amount=refund_data.amount,
        reason=refund_data.reason,
        notes=refund_data.notes,
        status="created",
    )
    db.add(refund)
    try:
        await db.commit()
        await db.refresh(refund)
    except IntegrityError as e:
        await db.rollback()
        raise conflict("Refund create violates a constraint") from e

    # 4) Вызываем TipTop
    try:
        tiptop = TipTopService()
        result = await tiptop.create_refund(
            payment_id=payment.external_id,
            amount=float(refund_data.amount),
            reason=refund_data.reason,
        )

        # 5) Обновляем refund + payment
        refund.external_id = result.get("refund_id")
        refund.provider_data = _to_json_string(result)
        refund.status = "processing"
        payment.refunded_amount = (payment.refunded_amount or 0) + refund_data.amount
        await db.commit()

        # 6) Аудит
        audit_logger.log_data_change(
            user_id=current_user.id,
            action="payment_refund",
            resource_type="payment_refund",
            resource_id=str(refund.id),
            changes={"amount": float(refund.amount), "payment_id": payment.id},
        )

        # 7) Идемпотентность
        if hasattr(request.state, "idempotency_key"):
            await set_idempotency_result(request.state.idempotency_key, status.HTTP_200_OK)

        return {
            "message": "Refund created successfully",
            "refund_id": refund.id,
            "refund_number": refund.refund_number,
        }

    except Exception as e:
        refund.status = "failed"
        refund.notes = f"Failed: {e!s}"
        await db.commit()
        raise server_error(f"Failed to create refund: {e!s}")

# ---------------------------------------------------------------------
# POST /payments/webhooks/tiptop
# ---------------------------------------------------------------------

@router.post("/webhooks/tiptop", summary="Webhook TipTop Pay")
async def tiptop_webhook(
    request: Request,
    webhook_data: WebhookPayment,
    db: AsyncSession = Depends(get_db),
):
    # 1) Подпись вебхука
    signature = request.headers.get("X-Signature")
    if not signature:
        raise bad_request("Missing signature")

    tiptop = TipTopService()
    body = await request.body()
    if not tiptop.verify_webhook_signature(body, signature):
        raise bad_request("Invalid signature")

    # 2) Идемпотентность события (по event_id)
    if await ensure_webhook_idempotency(db, "tiptop", webhook_data.event_id):
        return {"message": "Webhook already processed"}

    # 3) Ищем платёж по нашему provider_invoice_id
    payment = (
        await db.execute(
            select(Payment).where(Payment.provider_invoice_id == webhook_data.provider_invoice_id)
        )
    ).scalar_one_or_none()

    if not payment:
        # Не валим ошибкой — логируем как «неизвестный»
        audit_logger.log_data_change(
            user_id=0,
            action="webhook_unknown_payment",
            resource_type="payment",
            resource_id=str(webhook_data.provider_invoice_id),
            changes={"event_id": webhook_data.event_id, "status": webhook_data.status},
        )
        return {"message": "Payment not found"}

    old_status = payment.status

    # 4) Синхронизация статуса
    if webhook_data.status == "success":
        payment.status = "success"
        payment.confirmed_at = datetime.utcnow()
        payment.receipt_url = webhook_data.receipt_url

        order = (
            await db.execute(select(Order).where(Order.id == payment.order_id))
        ).scalar_one_or_none()
        if order:
            order.status = "paid"

    elif webhook_data.status == "failed":
        payment.status = "failed"
        payment.failed_at = datetime.utcnow()
        payment.failure_reason = webhook_data.failure_reason

    elif webhook_data.status == "cancelled":
        payment.status = "cancelled"

    # Сохраняем «сырой» провайдерский payload
    payment.provider_data = _to_json_string(webhook_data.dict())
    await db.commit()

    audit_logger.log_data_change(
        user_id=0,
        action="webhook_processed",
        resource_type="payment",
        resource_id=str(payment.id),
        changes={"old_status": old_status, "new_status": payment.status, "event_id": webhook_data.event_id},
    )

    return {"message": "Webhook processed successfully"}

# ---------------------------------------------------------------------
# GET /payments/{payment_id}/status
# ---------------------------------------------------------------------

@router.get("/{payment_id}/status", summary="Проверка статуса платежа у провайдера")
async def check_payment_status(
    payment_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    payment = (
        await db.execute(
            select(Payment)
            .join(Order, Payment.order_id == Order.id)
            .where(and_(Payment.id == payment_id, _order_company_scope(current_user.company_id)))
        )
    ).scalar_one_or_none()
    if not payment:
        raise not_found("Payment not found")

    if not payment.external_id:
        return {"status": payment.status, "message": "Payment not yet processed by provider"}

    try:
        tiptop = TipTopService()
        status_result = await tiptop.get_payment_status(payment.external_id)
        provider_status = status_result.get("status")

        if provider_status and provider_status != payment.status:
            old_status = payment.status
            payment.status = provider_status
            payment.provider_data = _to_json_string(status_result)

            if provider_status == "success":
                order = (
                    await db.execute(select(Order).where(Order.id == payment.order_id))
                ).scalar_one_or_none()
                if order:
                    order.status = "paid"

            await db.commit()

            audit_logger.log_data_change(
                user_id=current_user.id,
                action="payment_status_update",
                resource_type="payment",
                resource_id=str(payment.id),
                changes={"old_status": old_status, "new_status": payment.status},
            )

        return {"status": payment.status, "provider_status": provider_status, "updated_at": payment.updated_at}

    except Exception as e:
        # Возвращаем локальный статус + сообщение об ошибке провайдера
        return {"status": payment.status, "error": f"Failed to check provider status: {e!s}"}
