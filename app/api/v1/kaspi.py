from __future__ import annotations

"""
app/api/v1/kaspi.py — Полный, боевой роутер интеграции с Kaspi.

Что реализовано (по ТЗ и договорённостям):
- POST   /api/v1/kaspi/connect                — единая точка «подключить магазин» (verify/save).
- POST   /api/v1/kaspi/tokens                 — создать/обновить токен магазина (upsert).
- GET    /api/v1/kaspi/tokens                 — список подключённых магазинов (алиасы).
- GET    /api/v1/kaspi/tokens/{store_name}    — карточка токена (маска + метаданные, без расшифровки).
- DELETE /api/v1/kaspi/tokens/{store_name}    — удалить токен (безвозвратно).
- GET    /api/v1/kaspi/health/{store}         — health-проверка адаптера Kaspi для конкретного магазина.
- POST   /api/v1/kaspi/orders                 — получить заказы (через адаптер).
- POST   /api/v1/kaspi/import                 — запустить импорт офферов (фид) в Kaspi.
- POST   /api/v1/kaspi/import/status          — проверить статус импорта офферов.
- POST   /api/v1/kaspi/orders/sync            — синхронизировать свежие заказы Kaspi в локальную БД.
- GET    /api/v1/kaspi/feed      — сгенерировать XML-фид активных товаров компании.
- POST   /api/v1/kaspi/availability/sync      — синхронизировать доступность одного товара.
- POST   /api/v1/kaspi/availability/bulk      — массовая синхронизация доступности по компании.
- GET    /api/v1/kaspi/_debug/ping            — диагностический ping.

Принципы:
- Предсказуемые ответы: 4xx/5xx с внятным detail, без «просто 500».
- Pydantic v2, SQLAlchemy 2.x (AsyncSession).
- Безопасность: наружу не отдаём сырые токены, только маска + метаданные.
- Расширяемость: аккуратные модели ввода/вывода; готово к будущим эндпоинтам.
"""

import inspect
import json
import logging
from typing import Any

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_async_db  # noqa — для совместимости импорт-алиас
from app.core.security import get_current_user, resolve_tenant_company_id

# Доменные зависимости/схемы:
from app.integrations.kaspi_adapter import KaspiAdapter, KaspiAdapterError
from app.models import Product
from app.models.company import Company
from app.models.kaspi_order_sync_state import KaspiOrderSyncState
from app.models.marketplace import KaspiStoreToken
from app.models.user import User
from app.schemas.kaspi import (
    ImportRequest,
    ImportStatusQuery,
    KaspiConnectIn,
    KaspiConnectOut,
    KaspiTokenIn,
    KaspiTokenOut,
    OrdersQuery,
)
from app.services.kaspi_service import KaspiService, KaspiSyncAlreadyRunning

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/kaspi", tags=["kaspi"])


# ----------------------------- Константы/утилиты -----------------------------

MASK_HEX_LEN = 10
MASK_CHAR = "..."


def normalize_name(name: str) -> str:
    return name.strip().lower()


async def _maybe_await(value):
    if inspect.isawaitable(value):
        return await value
    return value


async def _auth_user(current_user: User = Depends(get_current_user)) -> User:
    return current_user


def _resolve_company_id(current_user: User) -> int:
    return resolve_tenant_company_id(current_user, not_found_detail="Company not set")


# ------------------------------- Локальные схемы (deprecated - use app/schemas/kaspi.py) ------


class AvailabilitySyncIn(BaseModel):
    product_id: int = Field(..., ge=1, description="ID продукта в нашей БД")


class AvailabilityBulkIn(BaseModel):
    limit: int = Field(500, ge=1, le=5000, description="Максимум товаров за одну операцию")


class KaspiTokenMaskedOut(BaseModel):
    """Ответ для карточки токена без раскрытия секрета."""

    id: str
    store_name: str
    token_hex_masked: str
    created_at: Any
    updated_at: Any


@router.post(
    "/connect",
    response_model=KaspiConnectOut,
    status_code=status.HTTP_200_OK,
    summary="Kaspi onboarding: connect and configure store (main entry point)",
)
async def connect_store(
    body: KaspiConnectIn,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_db),
):
    """
    Main Kaspi onboarding endpoint:
    1) Resolve tenant company from current user.
    2) Validate and optionally verify token with Kaspi adapter.
    3) Update Company.name with provided company_name.
    4) Store encrypted token via KaspiStoreToken.upsert_token().
    5) Store optional private metadata in Company.settings JSON.
    6) Return only safe fields (no token, no metadata exposed).

    Requires: company_name (min_length=2).
    Optional: meta (private marketplace metadata, not exposed).
    """
    # Resolve company from current user (tenant isolation)
    company_id = _resolve_company_id(current_user)

    # Load Company
    result = await session.execute(sa.select(Company).where(Company.id == company_id))
    company = result.scalars().first()
    if not company:
        logger.error("Kaspi connect: company not found id=%s", company_id)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Company not found",
        )

    # Verify token if requested (before persisting)
    if body.verify:
        try:
            logger.info("Kaspi connect: verifying token for store=%s", body.store_name)
            adapter = KaspiAdapter()
            await _maybe_await(adapter.health(body.store_name))
            logger.info("Kaspi connect: verification succeeded for store=%s", body.store_name)
        except KaspiAdapterError as e:
            logger.warning("Kaspi connect: verification failed store=%s error=%s", body.store_name, e)
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Token verification failed: {str(e)}",
            )

    # Update Company with provided company_name
    company.name = body.company_name.strip()
    company.kaspi_store_id = body.store_name

    # Store private metadata in Company.settings if provided
    if body.meta:
        try:
            settings_dict = {}
            if company.settings:
                try:
                    settings_dict = json.loads(company.settings)
                except (json.JSONDecodeError, TypeError):
                    settings_dict = {}
            settings_dict["kaspi_meta"] = body.meta
            company.settings = json.dumps(settings_dict)
            logger.debug("Kaspi connect: stored private metadata for company_id=%s", company_id)
        except Exception as e:
            logger.warning("Kaspi connect: failed to store metadata company_id=%s error=%s", company_id, e)
            # Don't fail the entire request if metadata storage fails

    # Upsert encrypted token (never expose plaintext)
    try:
        logger.info("Kaspi connect: upserting token for store=%s", body.store_name)
        await KaspiStoreToken.upsert_token(session, body.store_name, body.token)
        logger.info("Kaspi connect: token upserted for store=%s company_id=%s", body.store_name, company_id)
    except Exception as e:
        logger.error("Kaspi connect: token upsert failed store=%s error=%s", body.store_name, e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to save token: {str(e)}",
        )

    # Commit all changes in single transaction
    try:
        await session.commit()
        logger.info("Kaspi connect: transaction committed company_id=%s store=%s", company_id, body.store_name)
    except Exception as e:
        await session.rollback()
        logger.error("Kaspi connect: commit failed company_id=%s error=%s", company_id, e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to save configuration: {str(e)}",
        )

    return KaspiConnectOut(
        store_name=body.store_name,
        company_id=company_id,
        connected=True,
        message="Successfully connected to Kaspi store",
    )


# ================================= TOKENS ====================================


@router.post(
    "/tokens",
    response_model=KaspiTokenOut,
    status_code=status.HTTP_201_CREATED,
    summary="Создать/обновить токен магазина",
)
async def upsert_token(
    payload: KaspiTokenIn,
    session: AsyncSession = Depends(get_async_db),
):
    try:
        await KaspiStoreToken.upsert_token(session, payload.store_name, payload.token)
    except Exception as e:
        logger.error("Kaspi upsert_token failed: store=%s err=%s", payload.store_name, e)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))
    return KaspiTokenOut(store_name=payload.store_name)


@router.get(
    "/tokens",
    response_model=list[KaspiTokenOut],
    summary="Список подключённых магазинов",
)
async def list_tokens(session: AsyncSession = Depends(get_async_db)):
    try:
        stores = await KaspiStoreToken.list_stores(session)
    except Exception as e:
        logger.error("Kaspi list_tokens failed: %s", e)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))
    return [KaspiTokenOut(store_name=s) for s in stores]


@router.get(
    "/tokens/{store_name}",
    response_model=KaspiTokenMaskedOut,
    summary="Карточка токена (маска + метаданные)",
)
async def get_token_by_store_name(
    store_name: str,
    session: AsyncSession = Depends(get_async_db),
):
    """
    Возвращает запись токена по имени магазина.
    Токен НЕ раскрываем — только маска (первые MASK_HEX_LEN hex-символов) и метаданные.
    """
    q = text(
        """
        SELECT
            id,
            store_name,
            left(encode(token_ciphertext,'hex'), :mask_len) || :mask_char AS token_hex_masked,
            created_at,
            updated_at
        FROM kaspi_store_tokens
        WHERE lower(trim(store_name)) = lower(trim(:name))
        LIMIT 1
        """
    ).bindparams(
        bindparam("mask_len", type_=sa.Integer),
        bindparam("mask_char", type_=sa.String),
        bindparam("name", type_=sa.String),
    )

    try:
        res = await session.execute(q, {"name": store_name, "mask_len": MASK_HEX_LEN, "mask_char": MASK_CHAR})
        row = res.mappings().first()
    except Exception as e:
        logger.error("Kaspi get_token_by_store_name failed: store=%s err=%s", store_name, e)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="db_error")

    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not Found")

    return KaspiTokenMaskedOut(
        id=str(row["id"]),
        store_name=row["store_name"],
        token_hex_masked=row["token_hex_masked"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


@router.delete(
    "/tokens/{store_name}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Удалить токен магазина",
)
async def delete_token(store_name: str, session: AsyncSession = Depends(get_async_db)):
    try:
        deleted = await KaspiStoreToken.delete_by_store(session, store_name)
        if not deleted:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not Found")
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Kaspi delete_token failed: store=%s err=%s", store_name, e)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ============================ Операции через адаптер ==========================


@router.get(
    "/health/{store}",
    summary="Проверка здоровья Kaspi API для магазина",
)
async def kaspi_health(store: str):
    try:
        return KaspiAdapter().health(store)
    except KaspiAdapterError as e:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(e))
    except Exception as e:
        logger.error("Kaspi health unexpected error: store=%s err=%s", store, e)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))


@router.post(
    "/orders",
    summary="Получить заказы из Kaspi (проксирование через адаптер)",
)
async def kaspi_orders(query: OrdersQuery):
    try:
        return KaspiAdapter().orders(query.store, state=query.state)
    except KaspiAdapterError as e:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(e))
    except Exception as e:
        logger.error("Kaspi orders unexpected error: payload=%s err=%s", query.model_dump(), e)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))


@router.post(
    "/import",
    summary="Запустить импорт офферов (фид) в Kaspi",
)
async def kaspi_import(req: ImportRequest):
    try:
        return KaspiAdapter().publish_feed(req.store, req.offers_json_path)
    except KaspiAdapterError as e:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(e))
    except Exception as e:
        logger.error("Kaspi import unexpected error: payload=%s err=%s", req.model_dump(), e)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))


@router.post(
    "/import/status",
    summary="Проверить статус импорта офферов в Kaspi",
)
async def kaspi_import_status(req: ImportStatusQuery):
    try:
        return KaspiAdapter().import_status(req.store, import_id=req.import_id)
    except KaspiAdapterError as e:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(e))
    except Exception as e:
        logger.error("Kaspi import_status unexpected error: payload=%s err=%s", req.model_dump(), e)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))


# ================================== Service ==================================


@router.post(
    "/orders/sync",
    summary="Синхронизировать последние заказы Kaspi в локальную БД",
)
async def kaspi_orders_sync(
    request: Request,
    current_user: User = Depends(_auth_user),
    session: AsyncSession = Depends(get_async_db),
):
    resolved_company_id: int | None = None
    svc: KaspiService | None = None
    try:
        resolved_company_id = _resolve_company_id(current_user)
        svc = KaspiService()
        request_id = getattr(getattr(request, "state", None), "request_id", None) if request else None
        return await svc.sync_orders(db=session, company_id=resolved_company_id, request_id=request_id)
    except KaspiSyncAlreadyRunning:
        raise HTTPException(status_code=status.HTTP_423_LOCKED, detail="kaspi sync already running")
    except Exception as e:
        svc = svc or KaspiService()
        error_code = svc.classify_sync_error(e)
        retry_after = svc.get_retry_after_seconds(e)
        logger.error("Kaspi orders sync failed: company_id=%s code=%s err=%s", resolved_company_id, error_code, e)

        if error_code == "kaspi_http_429":
            headers = {"Retry-After": str(retry_after)} if retry_after is not None else None
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="kaspi rate limited", headers=headers
            )

        if error_code in {"kaspi_timeout", "timeout"}:
            raise HTTPException(status_code=status.HTTP_504_GATEWAY_TIMEOUT, detail="kaspi timeout")

        if error_code.startswith("kaspi_http_") or error_code == "kaspi_adapter_error":
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="kaspi upstream error")

        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="kaspi sync failed")


@router.get(
    "/feed",
    summary="Сгенерировать XML-фид активных товаров компании",
    response_class=Response,
)
async def kaspi_generate_feed(
    current_user: User = Depends(_auth_user),
    session: AsyncSession = Depends(get_async_db),
):
    resolved_company_id: int | None = None
    try:
        resolved_company_id = _resolve_company_id(current_user)
        svc = KaspiService()
        xml_body = await svc.generate_product_feed(company_id=resolved_company_id, db=session)
        return Response(content=xml_body, media_type="application/xml")
    except HTTPException:
        raise
    except RuntimeError as e:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(e))
    except Exception as e:
        logger.exception("Kaspi generate feed unexpected error: company_id=%s", resolved_company_id)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))


@router.post(
    "/availability/sync",
    summary="Синхронизировать доступность (stock) одного товара в Kaspi",
)
async def kaspi_availability_sync_one(
    payload: AvailabilitySyncIn,
    current_user: User = Depends(_auth_user),
    session: AsyncSession = Depends(get_async_db),
):
    try:
        resolved_company_id = _resolve_company_id(current_user)
        res = await session.execute(
            sa.select(Product).where(Product.id == payload.product_id, Product.company_id == resolved_company_id)
        )
        product: Product | None = res.scalar_one_or_none()
        if not product:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product not found")

        svc = KaspiService()
        ok = await svc.sync_product_availability(product)
        return {"ok": bool(ok)}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Kaspi availability sync one failed: payload=%s err=%s", payload.model_dump(), e)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(e))


@router.post(
    "/availability/bulk",
    summary="Массовая синхронизация доступности активных товаров компании",
)
async def kaspi_availability_bulk(
    payload: AvailabilityBulkIn,
    current_user: User = Depends(_auth_user),
    session: AsyncSession = Depends(get_async_db),
):
    try:
        resolved_company_id = _resolve_company_id(current_user)
        svc = KaspiService()
        stats = await svc.bulk_sync_availability(company_id=resolved_company_id, db=session, limit=payload.limit)
        return stats
    except Exception as e:
        logger.error("Kaspi availability bulk failed: payload=%s err=%s", payload.model_dump(), e)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(e))


class KaspiSyncStateOut(BaseModel):
    watermark: Any | None = None
    last_success_at: Any | None = None
    last_attempt_at: Any | None = None
    last_duration_ms: int | None = None
    last_result: str | None = None
    last_fetched: int | None = None
    last_inserted: int | None = None
    last_updated: int | None = None
    last_error_at: Any | None = None
    last_error_code: str | None = None
    last_error_message: str | None = None


class KaspiSyncOpsOut(KaspiSyncStateOut):
    lock_available: bool


@router.get(
    "/orders/sync/state",
    summary="Текущее состояние синхронизации заказов Kaspi",
    response_model=KaspiSyncStateOut,
)
async def kaspi_orders_sync_state(
    current_user: User = Depends(_auth_user),
    session: AsyncSession = Depends(get_async_db),
):
    company_id = _resolve_company_id(current_user)
    res = await session.execute(sa.select(KaspiOrderSyncState).where(KaspiOrderSyncState.company_id == company_id))
    state = res.scalar_one_or_none()
    watermark = getattr(state, "last_synced_at", None) if state else None
    last_success_at = getattr(state, "last_synced_at", None) if state else None
    last_attempt_at = getattr(state, "last_attempt_at", None) if state else None
    last_duration_ms = getattr(state, "last_duration_ms", None) if state else None
    last_result = getattr(state, "last_result", None) if state else None
    last_fetched = getattr(state, "last_fetched", None) if state else None
    last_inserted = getattr(state, "last_inserted", None) if state else None
    last_updated = getattr(state, "last_updated", None) if state else None
    last_error_at = getattr(state, "last_error_at", None) if state else None
    last_error_code = getattr(state, "last_error_code", None) if state else None
    last_error_message = getattr(state, "last_error_message", None) if state else None
    return KaspiSyncStateOut(
        watermark=watermark,
        last_success_at=last_success_at,
        last_attempt_at=last_attempt_at,
        last_duration_ms=last_duration_ms,
        last_result=last_result,
        last_fetched=last_fetched,
        last_inserted=last_inserted,
        last_updated=last_updated,
        last_error_at=last_error_at,
        last_error_code=last_error_code,
        last_error_message=last_error_message,
    )


@router.get(
    "/orders/sync/ops",
    summary="Операционный статус синхронизации заказов Kaspi (state + lock)",
    response_model=KaspiSyncOpsOut,
)
async def kaspi_orders_sync_ops(
    current_user: User = Depends(_auth_user),
    session: AsyncSession = Depends(get_async_db),
):
    company_id = _resolve_company_id(current_user)
    res = await session.execute(sa.select(KaspiOrderSyncState).where(KaspiOrderSyncState.company_id == company_id))
    state = res.scalar_one_or_none()

    watermark = getattr(state, "last_synced_at", None) if state else None
    last_success_at = getattr(state, "last_synced_at", None) if state else None
    last_attempt_at = getattr(state, "last_attempt_at", None) if state else None
    last_duration_ms = getattr(state, "last_duration_ms", None) if state else None
    last_result = getattr(state, "last_result", None) if state else None
    last_fetched = getattr(state, "last_fetched", None) if state else None
    last_inserted = getattr(state, "last_inserted", None) if state else None
    last_updated = getattr(state, "last_updated", None) if state else None
    last_error_at = getattr(state, "last_error_at", None) if state else None
    last_error_code = getattr(state, "last_error_code", None) if state else None
    last_error_message = getattr(state, "last_error_message", None) if state else None

    svc = KaspiService()
    lock_available = False
    try:
        lock_available = await svc.check_lock_available(session, company_id)
    except Exception:
        lock_available = False

    return KaspiSyncOpsOut(
        watermark=watermark,
        last_success_at=last_success_at,
        last_attempt_at=last_attempt_at,
        last_duration_ms=last_duration_ms,
        last_result=last_result,
        last_fetched=last_fetched,
        last_inserted=last_inserted,
        last_updated=last_updated,
        last_error_at=last_error_at,
        last_error_code=last_error_code,
        last_error_message=last_error_message,
        lock_available=bool(lock_available),
    )


# ================================= DEBUG =====================================


@router.get("/_debug/ping", summary="Kaspi debug ping")
def kaspi_debug_ping():
    return {"ok": True, "module": "kaspi", "prefix": router.prefix}


# ============================= AUTO-SYNC ADMIN ===============================


class KaspiAutoSyncStatusOut(BaseModel):
    """Ответ о статусе последнего запуска авто-синхронизации с конфигурацией и видимостью scheduler."""

    # Configuration
    enabled: bool = Field(..., description="Включена ли автоматическая синхронизация")
    interval_minutes: int = Field(0, description="Интервал синхронизации в минутах")
    max_concurrency: int = Field(0, description="Максимум параллельных синхронизаций")

    # Scheduler state
    job_registered: bool = Field(False, description="Зарегистрирована ли задача в scheduler")
    scheduler_running: bool | None = Field(None, description="Запущен ли scheduler (если доступно)")

    # Last run summary
    last_run_at: str | None = Field(None, description="ISO время последнего запуска")
    eligible_companies: int = Field(0, description="Сколько компаний подходят для синхронизации")
    success: int = Field(0, description="Успешно синхронизировано")
    locked: int = Field(0, description="Заблокировано (уже выполняется)")
    failed: int = Field(0, description="Неуспешно (ошибка)")


@router.get(
    "/autosync/status",
    summary="Статус автоматической синхронизации заказов",
    response_model=KaspiAutoSyncStatusOut,
)
async def kaspi_autosync_status(
    current_user: User = Depends(_auth_user),
):
    """
    Возвращает статус последнего запуска автоматической синхронизации заказов Kaspi
    с конфигурацией и видимостью scheduler.
    Не требует админских прав, но показывает глобальную статистику по всем компаниям.
    """
    from app.core.config import settings

    # Получаем configuration
    enabled = getattr(settings, "KASPI_AUTOSYNC_ENABLED", False)
    interval_minutes = getattr(settings, "KASPI_AUTOSYNC_INTERVAL_MINUTES", 15)
    max_concurrency = getattr(settings, "KASPI_AUTOSYNC_MAX_CONCURRENCY", 3)

    # Проверяем scheduler state
    job_registered = False
    scheduler_running = None
    try:
        from app.worker.scheduler_worker import scheduler

        scheduler_running = scheduler.running
        job = scheduler.get_job("kaspi_autosync")
        job_registered = job is not None
    except Exception:
        # If scheduler not available, we still return safe defaults
        pass

    # Получаем last run summary (safe defaults if autosync disabled)
    last_run_at = None
    eligible_companies = 0
    success = 0
    locked = 0
    failed = 0

    try:
        from app.worker.kaspi_autosync import get_last_run_summary

        summary = get_last_run_summary()
        last_run_at = summary.get("last_run_at")
        eligible_companies = summary.get("eligible_companies", 0)
        success = summary.get("success", 0)
        locked = summary.get("locked", 0)
        failed = summary.get("failed", 0)
    except ImportError:
        # Module not available, but we still return valid response
        pass

    return KaspiAutoSyncStatusOut(
        enabled=enabled,
        interval_minutes=interval_minutes,
        max_concurrency=max_concurrency,
        job_registered=job_registered,
        scheduler_running=scheduler_running,
        last_run_at=last_run_at,
        eligible_companies=eligible_companies,
        success=success,
        locked=locked,
        failed=failed,
    )


@router.post(
    "/autosync/trigger",
    summary="Ручной запуск автоматической синхронизации",
    response_model=KaspiAutoSyncStatusOut,
)
async def kaspi_autosync_trigger(
    current_user: User = Depends(_auth_user),
):
    """
    Запускает синхронизацию заказов Kaspi для всех активных компаний вручную.
    Полезно для диагностики или немедленного обновления без ожидания следующего цикла.
    """
    from app.core.config import settings

    enabled = getattr(settings, "KASPI_AUTOSYNC_ENABLED", False)

    if not enabled:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Kaspi auto-sync is disabled. Set KASPI_AUTOSYNC_ENABLED=true to enable.",
        )

    # Можно добавить проверку на админские права:
    # if not current_user.is_admin:
    #     raise HTTPException(status_code=403, detail="Admin only")

    try:
        from app.worker.kaspi_autosync import run_kaspi_autosync

        # Запускаем синхронно (блокирующий вызов)
        run_kaspi_autosync()

        # Возвращаем обновлённую статистику
        from app.worker.kaspi_autosync import get_last_run_summary

        summary = get_last_run_summary()
        return KaspiAutoSyncStatusOut(
            enabled=True,
            last_run_at=summary.get("last_run_at"),
            eligible_companies=summary.get("eligible_companies", 0),
            success=summary.get("success", 0),
            locked=summary.get("locked", 0),
            failed=summary.get("failed", 0),
        )
    except ImportError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Kaspi auto-sync module not available",
        )
