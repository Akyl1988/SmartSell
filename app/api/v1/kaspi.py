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
from datetime import datetime, timedelta
from typing import Any

import httpx
import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_async_db  # noqa — для совместимости импорт-алиас
from app.core.logging import get_logger
from app.core.security import get_current_user, resolve_tenant_company_id

# Доменные зависимости/схемы:
from app.integrations.kaspi_adapter import KaspiAdapter, KaspiAdapterError
from app.models import Product
from app.models.company import Company
from app.models.kaspi_catalog_product import KaspiCatalogProduct
from app.models.kaspi_feed_export import KaspiFeedExport
from app.models.kaspi_goods_import import KaspiGoodsImport
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
from app.services.kaspi_goods_client import KaspiGoodsClient, KaspiNotAuthenticated
from app.services.kaspi_service import KaspiService, KaspiSyncAlreadyRunning

logger = get_logger(__name__)
router = APIRouter(prefix="/api/v1/kaspi", tags=["kaspi"])


# ----------------------------- Константы/утилиты -----------------------------

MASK_HEX_LEN = 10
MASK_CHAR = "..."
STATUS_LAST_ERROR_MAX_LEN = 500


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


async def _resolve_kaspi_token(session: AsyncSession, company_id: int) -> tuple[str, str]:
    company = (await session.execute(sa.select(Company).where(Company.id == company_id))).scalars().first()
    if not company:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Company not found")
    store_name = (company.kaspi_store_id or "").strip()
    if not store_name:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="kaspi_store_not_configured")
    token = await KaspiStoreToken.get_token(session, store_name)
    if not token:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="kaspi_token_not_found")
    return store_name, token


def _extract_import_code(payload: dict) -> str | None:
    return payload.get("importCode") or payload.get("import_code") or payload.get("code") or payload.get("id")


def _product_to_goods_payload(product: Product) -> dict[str, Any]:
    sku = product.sku or f"PID-{product.id}"
    return {
        "sku": sku,
        "name": product.name or sku,
        "price": float(product.price) if product.price is not None else None,
        "quantity": int(product.stock_quantity or 0),
        "isActive": bool(product.is_active),
    }


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
    2) Validate and optionally verify token with Kaspi HTTP API (NOT PowerShell adapter).
    3) Update Company.name with provided company_name.
    4) Store encrypted token via KaspiStoreToken.upsert_token().
    5) Store optional private metadata in Company.settings JSON.
    6) Return only safe fields (no token, no metadata exposed).

    Requires: company_name (min_length=2).
    Optional: meta (private marketplace metadata, not exposed).

    Token verification (verify=true):
    - Uses KaspiService.verify_token() which makes minimal HTTP call to Kaspi API
    - 401/403 -> returns 400 with detail="kaspi_invalid_token"
    - Network/timeout errors -> returns 502 with detail="kaspi_upstream_error"
    - Never calls KaspiAdapter (no PowerShell dependency)
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

    # Verify token if requested (before persisting) - HTTP only, no PowerShell
    if body.verify:
        try:
            logger.info("Kaspi connect: verifying token via HTTP for store=%s", body.store_name)
            kaspi_service = KaspiService()
            await kaspi_service.verify_token(store_name=body.store_name, token=body.token)
            logger.info("Kaspi connect: HTTP verification succeeded for store=%s", body.store_name)
        except Exception as e:
            # Handle different error types
            import httpx

            if isinstance(e, httpx.HTTPStatusError):
                if e.response.status_code in (401, 403):
                    # Invalid token
                    logger.warning(
                        "Kaspi connect: invalid token store=%s status=%s", body.store_name, e.response.status_code
                    )
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="kaspi_invalid_token",
                    )
                else:
                    # Other HTTP errors are upstream problems
                    logger.error(
                        "Kaspi connect: upstream HTTP error store=%s status=%s", body.store_name, e.response.status_code
                    )
                    raise HTTPException(
                        status_code=status.HTTP_502_BAD_GATEWAY,
                        detail="kaspi_upstream_error",
                    )
            elif isinstance(e, httpx.TimeoutException | httpx.NetworkError):
                # Network/timeout errors
                logger.error("Kaspi connect: network error store=%s error=%s", body.store_name, type(e).__name__)
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail="kaspi_upstream_error",
                )
            else:
                # Unexpected errors
                logger.error("Kaspi connect: verification error store=%s error=%s", body.store_name, str(e))
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail="kaspi_upstream_error",
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

    # Scheduler state (mutual exclusion observability)
    runner_enabled: bool = Field(False, description="Включен ли main.py runner loop (ENABLE_KASPI_SYNC_RUNNER)")
    scheduler_job_effective_enabled: bool = Field(
        False, description="Включена ли APScheduler job после mutual exclusion"
    )
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
    request: Request,
    current_user: User = Depends(_auth_user),
):
    """
    Возвращает статус последнего запуска автоматической синхронизации заказов Kaspi
    с конфигурацией и видимостью scheduler.
    Не требует админских прав, но показывает глобальную статистику по всем компаниям.
    """
    import os

    from app.core.config import settings

    # Получаем configuration
    enabled = getattr(settings, "KASPI_AUTOSYNC_ENABLED", False)
    interval_minutes = getattr(settings, "KASPI_AUTOSYNC_INTERVAL_MINUTES", 15)
    max_concurrency = getattr(settings, "KASPI_AUTOSYNC_MAX_CONCURRENCY", 3)

    # Check mutual exclusion state
    runner_enabled = False
    scheduler_job_effective_enabled = False
    try:
        from app.worker.scheduler_worker import _env_truthy, should_register_kaspi_autosync

        runner_enabled = _env_truthy(os.getenv("ENABLE_KASPI_SYNC_RUNNER", "0"))
        scheduler_job_effective_enabled = should_register_kaspi_autosync()
    except ImportError as e:
        logger.debug(
            "scheduler_worker unavailable for autosync status",
            error=str(e),
            request_id=getattr(request.state, "request_id", None),
        )
    except Exception as e:
        logger.warning(
            "Failed to check scheduler_worker mutual exclusion state",
            error=str(e),
            request_id=getattr(request.state, "request_id", None),
        )

    # Проверяем scheduler state
    job_registered = False
    scheduler_running = None
    try:
        from app.worker.scheduler_worker import scheduler

        scheduler_running = scheduler.running
        job = scheduler.get_job("kaspi_autosync")
        job_registered = job is not None
    except ImportError as e:
        logger.debug(
            "APScheduler not available for autosync status",
            error=str(e),
            request_id=getattr(request.state, "request_id", None),
        )
    except Exception as e:
        logger.warning(
            "Failed to get APScheduler job state",
            error=str(e),
            request_id=getattr(request.state, "request_id", None),
        )

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
    except ImportError as e:
        logger.debug(
            "kaspi_autosync module unavailable for last_run_summary",
            error=str(e),
            request_id=getattr(request.state, "request_id", None),
        )
    except Exception as e:
        logger.warning(
            "Failed to get kaspi_autosync last_run_summary",
            error=str(e),
            request_id=getattr(request.state, "request_id", None),
        )

    return KaspiAutoSyncStatusOut(
        enabled=enabled,
        interval_minutes=interval_minutes,
        max_concurrency=max_concurrency,
        runner_enabled=runner_enabled,
        scheduler_job_effective_enabled=scheduler_job_effective_enabled,
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


# ============================= CATALOG PRODUCTS ==============================


class KaspiProductSyncOut(BaseModel):
    """Response model for catalog sync operation."""

    ok: bool
    company_id: int
    fetched: int
    inserted: int
    updated: int


class KaspiProductOut(BaseModel):
    """Response model for single product in catalog list."""

    offer_id: str
    name: str | None = None
    sku: str | None = None
    price: str | None = None
    qty: int | None = None
    is_active: bool


class KaspiProductListOut(BaseModel):
    """Response model for catalog products list."""

    items: list[KaspiProductOut]
    total: int
    limit: int
    offset: int


class KaspiGoodsImportIn(BaseModel):
    product_ids: list[int] | None = None
    payload: list[dict[str, Any]] | None = None
    content_type: str | None = None


class KaspiGoodsImportOut(BaseModel):
    ok: bool
    import_code: str
    status: str


class KaspiGoodsStatusOut(BaseModel):
    import_code: str
    status: str
    payload: dict[str, Any] | None = None


class KaspiGoodsResultOut(BaseModel):
    import_code: str
    status: str
    payload: dict[str, Any] | None = None


class KaspiTokenHealthOut(BaseModel):
    ok: bool
    orders_http: int
    goods_http: int
    cause: str | None = None


class KaspiTokenSelftestOut(BaseModel):
    orders_http: int
    goods_schema_http: int
    goods_categories_http: int
    goods_access: str | None = None
    orders_error: str | None = None


@router.post(
    "/products/sync",
    summary="Синхронизировать каталог Kaspi в локальную БД",
    response_model=KaspiProductSyncOut,
)
async def kaspi_products_sync(
    current_user: User = Depends(_auth_user),
    session: AsyncSession = Depends(get_async_db),
):
    """
    Синхронизирует каталог продуктов Kaspi для текущей компании.
    Использует tenant isolation через resolved_company_id.
    Идемпотентен: повторный запуск обновляет существующие записи.
    """
    from app.services.kaspi_products_sync_service import sync_kaspi_catalog_products

    company_id = _resolve_company_id(current_user)

    try:
        result = await sync_kaspi_catalog_products(session, company_id)
        return KaspiProductSyncOut(**result)
    except ValueError as e:
        detail = str(e) or "kaspi_sync_not_configured"
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=detail,
        )
    except Exception as e:
        logger.error("Kaspi products sync failed: company_id=%s error=%s", company_id, e)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to sync products from Kaspi",
        )


# ============================= GOODS API ==============================


@router.get(
    "/goods/schema",
    summary="Kaspi goods import schema",
)
async def kaspi_goods_schema(
    current_user: User = Depends(_auth_user),
    session: AsyncSession = Depends(get_async_db),
):
    company_id = _resolve_company_id(current_user)
    _, token = await _resolve_kaspi_token(session, company_id)
    client = KaspiGoodsClient(token=token, base_url="https://kaspi.kz")
    try:
        return await client.get_schema()
    except KaspiNotAuthenticated as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="NOT_AUTHENTICATED") from exc


@router.get(
    "/goods/categories",
    summary="Kaspi goods categories",
)
async def kaspi_goods_categories(
    current_user: User = Depends(_auth_user),
    session: AsyncSession = Depends(get_async_db),
):
    company_id = _resolve_company_id(current_user)
    _, token = await _resolve_kaspi_token(session, company_id)
    client = KaspiGoodsClient(token=token, base_url="https://kaspi.kz")
    try:
        return await client.get_categories()
    except KaspiNotAuthenticated as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="NOT_AUTHENTICATED") from exc


@router.get(
    "/goods/attributes",
    summary="Kaspi goods attributes for category",
)
async def kaspi_goods_attributes(
    category: str,
    current_user: User = Depends(_auth_user),
    session: AsyncSession = Depends(get_async_db),
):
    company_id = _resolve_company_id(current_user)
    _, token = await _resolve_kaspi_token(session, company_id)
    client = KaspiGoodsClient(token=token, base_url="https://kaspi.kz")
    try:
        return await client.get_attributes(category_code=category)
    except KaspiNotAuthenticated as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="NOT_AUTHENTICATED") from exc


@router.get(
    "/goods/attribute-values",
    summary="Kaspi goods attribute values",
)
async def kaspi_goods_attribute_values(
    category: str,
    attribute: str,
    current_user: User = Depends(_auth_user),
    session: AsyncSession = Depends(get_async_db),
):
    company_id = _resolve_company_id(current_user)
    _, token = await _resolve_kaspi_token(session, company_id)
    client = KaspiGoodsClient(token=token, base_url="https://kaspi.kz")
    try:
        return await client.get_attribute_values(category_code=category, attribute_code=attribute)
    except KaspiNotAuthenticated as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="NOT_AUTHENTICATED") from exc


@router.post(
    "/goods/import",
    summary="Kaspi goods import",
    response_model=KaspiGoodsImportOut,
)
async def kaspi_goods_import(
    body: KaspiGoodsImportIn,
    current_user: User = Depends(_auth_user),
    session: AsyncSession = Depends(get_async_db),
):
    company_id = _resolve_company_id(current_user)
    _, token = await _resolve_kaspi_token(session, company_id)

    payload: list[dict[str, Any]]
    if body.payload:
        payload = body.payload
    elif body.product_ids:
        res = await session.execute(
            sa.select(Product).where(sa.and_(Product.company_id == company_id, Product.id.in_(body.product_ids)))
        )
        products = res.scalars().all()
        if not products:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="products_not_found")
        payload = [_product_to_goods_payload(p) for p in products]
    else:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="payload_or_product_ids_required")

    client = KaspiGoodsClient(token=token, base_url="https://kaspi.kz")
    try:
        response = await client.post_import(payload, content_type=body.content_type)
    except KaspiNotAuthenticated as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="NOT_AUTHENTICATED") from exc

    import_code = _extract_import_code(response)
    if not import_code:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="kaspi_import_code_missing")

    status_value = response.get("status") or "submitted"

    record = KaspiGoodsImport(
        company_id=company_id,
        created_by_user_id=current_user.id,
        import_code=str(import_code),
        status=str(status_value),
        request_payload=payload,
        result_payload=None,
        last_error=None,
    )
    session.add(record)
    await session.commit()

    return KaspiGoodsImportOut(ok=True, import_code=str(import_code), status=str(status_value))


@router.get(
    "/goods/import/{code}",
    summary="Kaspi goods import status",
    response_model=KaspiGoodsStatusOut,
)
async def kaspi_goods_import_status(
    code: str,
    current_user: User = Depends(_auth_user),
    session: AsyncSession = Depends(get_async_db),
):
    company_id = _resolve_company_id(current_user)
    _, token = await _resolve_kaspi_token(session, company_id)
    client = KaspiGoodsClient(token=token, base_url="https://kaspi.kz")
    try:
        response = await client.get_import_status(import_code=code)
    except KaspiNotAuthenticated as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="NOT_AUTHENTICATED") from exc

    status_value = response.get("status") or "unknown"

    res = await session.execute(
        sa.select(KaspiGoodsImport).where(
            sa.and_(KaspiGoodsImport.company_id == company_id, KaspiGoodsImport.import_code == code)
        )
    )
    record = res.scalars().first()
    if record:
        record.status = str(status_value)
        record.result_payload = response
        await session.commit()

    return KaspiGoodsStatusOut(import_code=code, status=str(status_value), payload=response)


@router.get(
    "/goods/import/{code}/result",
    summary="Kaspi goods import result",
    response_model=KaspiGoodsResultOut,
)
async def kaspi_goods_import_result(
    code: str,
    current_user: User = Depends(_auth_user),
    session: AsyncSession = Depends(get_async_db),
):
    company_id = _resolve_company_id(current_user)
    _, token = await _resolve_kaspi_token(session, company_id)
    client = KaspiGoodsClient(token=token, base_url="https://kaspi.kz")
    try:
        response = await client.get_import_result(import_code=code)
    except KaspiNotAuthenticated as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="NOT_AUTHENTICATED") from exc

    status_value = response.get("status") or "unknown"

    res = await session.execute(
        sa.select(KaspiGoodsImport).where(
            sa.and_(KaspiGoodsImport.company_id == company_id, KaspiGoodsImport.import_code == code)
        )
    )
    record = res.scalars().first()
    if record:
        record.status = str(status_value)
        record.result_payload = response
        await session.commit()

    return KaspiGoodsResultOut(import_code=code, status=str(status_value), payload=response)


@router.get(
    "/token/health",
    summary="Kaspi token health",
    response_model=KaspiTokenHealthOut,
)
async def kaspi_token_health(
    current_user: User = Depends(_auth_user),
    session: AsyncSession = Depends(get_async_db),
):
    company_id = _resolve_company_id(current_user)
    _, token = await _resolve_kaspi_token(session, company_id)

    now = datetime.utcnow()
    ge_ms = int((now - timedelta(days=14)).timestamp() * 1000)
    le_ms = int(now.timestamp() * 1000)

    orders_url = "https://kaspi.kz/shop/api/v2/orders"
    orders_params = {
        "page[number]": 0,
        "page[size]": 1,
        "filter[orders][creationDate][$ge]": ge_ms,
        "filter[orders][creationDate][$le]": le_ms,
    }

    orders_headers = {
        "X-Auth-Token": token,
        "Accept": "application/vnd.api+json",
    }

    goods_headers = {
        "X-Auth-Token": token,
        "Accept": "application/json",
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        orders_resp = await client.get(orders_url, headers=orders_headers, params=orders_params)
        goods_resp = await client.get("https://kaspi.kz/shop/api/products/import/schema", headers=goods_headers)

    cause = None
    if orders_resp.status_code == 401 or goods_resp.status_code == 401:
        cause = "NOT_AUTHENTICATED"

    return KaspiTokenHealthOut(
        ok=cause is None,
        orders_http=orders_resp.status_code,
        goods_http=goods_resp.status_code,
        cause=cause,
    )


@router.get(
    "/token/selftest",
    summary="Kaspi token self-test",
    response_model=KaspiTokenSelftestOut,
)
async def kaspi_token_selftest(
    current_user: User = Depends(_auth_user),
    session: AsyncSession = Depends(get_async_db),
):
    company_id = _resolve_company_id(current_user)
    _, token = await _resolve_kaspi_token(session, company_id)

    now = datetime.utcnow()
    ge_ms = int((now - timedelta(days=14)).timestamp() * 1000)
    le_ms = int(now.timestamp() * 1000)

    orders_url = "https://kaspi.kz/shop/api/v2/orders"
    orders_params = {
        "page[size]": 1,
        "filter[orders][state]": "NEW",
        "filter[orders][creationDate][$ge]": ge_ms,
        "filter[orders][creationDate][$le]": le_ms,
    }
    orders_headers = {
        "X-Auth-Token": token,
        "Accept": "application/vnd.api+json",
        "Content-Type": "application/vnd.api+json",
    }

    goods_headers = {
        "X-Auth-Token": token,
        "Accept": "application/json",
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        orders_resp = await client.get(orders_url, headers=orders_headers, params=orders_params)
        goods_schema_resp = await client.get(
            "https://kaspi.kz/shop/api/products/import/schema",
            headers=goods_headers,
        )
        goods_categories_resp = await client.get(
            "https://kaspi.kz/shop/api/products/classification/categories",
            headers=goods_headers,
        )

    if orders_resp.status_code == 401:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="NOT_AUTHENTICATED")

    orders_error = None
    if orders_resp.status_code >= 400:
        orders_error = "orders_request_failed"

    goods_access = None
    if orders_resp.status_code == 200 and (
        goods_schema_resp.status_code == 401 or goods_categories_resp.status_code == 401
    ):
        goods_access = "missing_or_not_enabled"

    return KaspiTokenSelftestOut(
        orders_http=orders_resp.status_code,
        goods_schema_http=goods_schema_resp.status_code,
        goods_categories_http=goods_categories_resp.status_code,
        goods_access=goods_access,
        orders_error=orders_error,
    )


@router.get(
    "/products",
    summary="Получить список каталога Kaspi",
    response_model=KaspiProductListOut,
)
async def kaspi_products_list(
    limit: int = 50,
    offset: int = 0,
    q: str | None = None,
    current_user: User = Depends(_auth_user),
    session: AsyncSession = Depends(get_async_db),
):
    """
    Возвращает список продуктов каталога Kaspi для текущей компании.

    Args:
        limit: Максимум записей (default 50, max 200)
        offset: Смещение для пагинации (default 0)
        q: Опциональный поиск по name/sku (ILIKE)

    Returns:
        Список продуктов с безопасными полями (без raw)
    """
    company_id = _resolve_company_id(current_user)

    # Validate limit
    limit = max(1, min(limit, 200))
    offset = max(0, offset)

    try:
        # Build query
        query = sa.select(KaspiCatalogProduct).where(KaspiCatalogProduct.company_id == company_id)

        # Optional search
        if q:
            search_pattern = f"%{q}%"
            query = query.where(
                sa.or_(
                    KaspiCatalogProduct.name.ilike(search_pattern),
                    KaspiCatalogProduct.sku.ilike(search_pattern),
                )
            )

        # Count total
        count_query = sa.select(sa.func.count()).select_from(query.subquery())
        total_result = await session.execute(count_query)
        total = total_result.scalar() or 0

        # Apply pagination
        query = query.limit(limit).offset(offset).order_by(KaspiCatalogProduct.id)

        # Execute
        result = await session.execute(query)
        products = result.scalars().all()

        # Map to response model (safe fields only)
        items = [
            KaspiProductOut(
                offer_id=p.offer_id,
                name=p.name,
                sku=p.sku,
                price=str(p.price) if p.price is not None else None,
                qty=p.qty,
                is_active=p.is_active,
            )
            for p in products
        ]

        return KaspiProductListOut(
            items=items,
            total=total,
            limit=limit,
            offset=offset,
        )

    except Exception as e:
        logger.error("Kaspi products list failed: company_id=%s error=%s", company_id, e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve products",
        )


# ============================= FEED EXPORTS ==============================


class KaspiFeedExportOut(BaseModel):
    """Response model for feed export metadata with retry diagnostics."""

    id: int
    kind: str
    format: str
    status: str  # generated, uploading, uploaded, failed
    checksum: str
    stats_json: dict | None = None
    last_error: str | None = None
    attempts: int = 0
    last_attempt_at: str | None = None
    uploaded_at: str | None = None
    duration_ms: int | None = None
    created_at: str | None = None
    updated_at: str | None = None


class KaspiFeedGenerateOut(BaseModel):
    """Response model for feed generation."""

    ok: bool
    export_id: int
    company_id: int
    total: int
    active: int
    checksum: str
    is_new: bool


class KaspiFeedUploadOut(BaseModel):
    """Response model for feed upload with retry diagnostics."""

    ok: bool
    export_id: int
    status: str
    error: str | None = None
    is_retryable: bool | None = None
    already_uploaded: bool = False
    upload_in_progress: bool = False


class KaspiFeedListOut(BaseModel):
    """Response model for feed exports list."""

    items: list[KaspiFeedExportOut]
    total: int
    limit: int
    offset: int


class KaspiStatusFeedOut(BaseModel):
    """Status summary for the latest products feed."""

    id: int
    status: str
    attempts: int = 0
    last_attempt_at: str | None = None
    uploaded_at: str | None = None
    duration_ms: int | None = None
    last_error: str | None = None
    created_at: str | None = None


class KaspiStatusFeedsOut(BaseModel):
    products_latest: KaspiStatusFeedOut | None = None


class KaspiCatalogStatusOut(BaseModel):
    total: int
    active: int
    last_updated_at: str | None = None


class KaspiOrdersSyncStatusOut(BaseModel):
    last_synced_at: str | None = None
    last_external_order_id: str | None = None
    last_attempt_at: str | None = None
    last_duration_ms: int | None = None
    last_result: str | None = None
    last_fetched: int | None = None
    last_inserted: int | None = None
    last_updated: int | None = None
    last_error_at: str | None = None
    last_error_code: str | None = None
    last_error_message: str | None = None
    updated_at: str | None = None


class KaspiHealthStatusOut(BaseModel):
    has_kaspi_token_configured: bool


class KaspiStatusOut(BaseModel):
    feeds: KaspiStatusFeedsOut
    catalog: KaspiCatalogStatusOut
    orders_sync: KaspiOrdersSyncStatusOut | None = None
    health: KaspiHealthStatusOut


@router.post(
    "/feeds/products/generate",
    summary="Сгенерировать фид продуктов для Kaspi",
    response_model=KaspiFeedGenerateOut,
)
async def kaspi_feed_generate_products(
    current_user: User = Depends(_auth_user),
    session: AsyncSession = Depends(get_async_db),
):
    """
    Генерирует фид продуктов для текущей компании.
    Идемпотентен: повторный вызов вернёт существующий фид если контент не изменился.
    """
    from app.services.kaspi_feed_export_service import generate_products_feed

    company_id = _resolve_company_id(current_user)

    try:
        result = await generate_products_feed(session, company_id)
        return KaspiFeedGenerateOut(**result)
    except Exception as e:
        logger.error("Kaspi feed generation failed: company_id=%s error=%s", company_id, e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to generate feed",
        )


@router.post(
    "/feeds/{export_id}/upload",
    summary="Загрузить фид на Kaspi",
    response_model=KaspiFeedUploadOut,
)
async def kaspi_feed_upload(
    export_id: int,
    current_user: User = Depends(_auth_user),
    session: AsyncSession = Depends(get_async_db),
):
    """
    Загружает фид на Kaspi. Текущий пользователь должен иметь доступ к компании фида.
    """
    from app.services.kaspi_feed_export_service import upload_feed_export

    company_id = _resolve_company_id(current_user)

    try:
        result = await upload_feed_export(session, export_id, company_id)
        if not result["ok"]:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=result.get("error", "Export not found"),
            )
        return KaspiFeedUploadOut(**result)
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Kaspi feed upload failed: export_id=%s company_id=%s error=%s", export_id, company_id, e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to upload feed",
        )


@router.get(
    "/feeds",
    summary="Получить список фидов",
    response_model=KaspiFeedListOut,
)
async def kaspi_feeds_list(
    kind: str | None = None,
    limit: int = 50,
    offset: int = 0,
    current_user: User = Depends(_auth_user),
    session: AsyncSession = Depends(get_async_db),
):
    """
    Возвращает список фидов для текущей компании с опциональной фильтрацией по kind.
    """
    from app.models.kaspi_feed_export import KaspiFeedExport

    company_id = _resolve_company_id(current_user)

    # Validate limit
    limit = max(1, min(limit, 200))
    offset = max(0, offset)

    try:
        # Build query
        query = sa.select(KaspiFeedExport).where(KaspiFeedExport.company_id == company_id)

        # Optional filter by kind
        if kind:
            query = query.where(KaspiFeedExport.kind == kind)

        # Count total
        count_query = sa.select(sa.func.count()).select_from(query.subquery())
        total_result = await session.execute(count_query)
        total = total_result.scalar() or 0

        # Apply pagination and ordering
        query = query.order_by(KaspiFeedExport.created_at.desc()).limit(limit).offset(offset)

        # Execute
        result = await session.execute(query)
        exports = result.scalars().all()

        # Map to response models
        items = [
            KaspiFeedExportOut(
                id=e.id,
                kind=e.kind,
                format=e.format,
                status=e.status,
                checksum=e.checksum,
                stats_json=e.stats_json,
                last_error=e.last_error,
                attempts=e.attempts or 0,
                last_attempt_at=e.last_attempt_at.isoformat() if e.last_attempt_at else None,
                uploaded_at=e.uploaded_at.isoformat() if e.uploaded_at else None,
                duration_ms=e.duration_ms,
                created_at=e.created_at.isoformat() if e.created_at else None,
                updated_at=e.updated_at.isoformat() if e.updated_at else None,
            )
            for e in exports
        ]

        return KaspiFeedListOut(
            items=items,
            total=total,
            limit=limit,
            offset=offset,
        )

    except Exception as e:
        logger.error("Kaspi feeds list failed: company_id=%s error=%s", company_id, e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve feeds",
        )


@router.get(
    "/feeds/{export_id}",
    summary="Получить метаданные фида",
    response_model=KaspiFeedExportOut,
)
async def kaspi_feed_get(
    export_id: int,
    current_user: User = Depends(_auth_user),
    session: AsyncSession = Depends(get_async_db),
):
    """
    Возвращает метаданные фида (без payload).
    """
    from app.models.kaspi_feed_export import KaspiFeedExport

    company_id = _resolve_company_id(current_user)

    try:
        stmt = sa.select(KaspiFeedExport).where(
            sa.and_(
                KaspiFeedExport.id == export_id,
                KaspiFeedExport.company_id == company_id,
            )
        )
        result = await session.execute(stmt)
        export = result.scalars().first()

        if not export:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Export not found",
            )

        return KaspiFeedExportOut(
            id=export.id,
            kind=export.kind,
            format=export.format,
            status=export.status,
            checksum=export.checksum,
            stats_json=export.stats_json,
            last_error=export.last_error,
            attempts=export.attempts or 0,
            last_attempt_at=export.last_attempt_at.isoformat() if export.last_attempt_at else None,
            uploaded_at=export.uploaded_at.isoformat() if export.uploaded_at else None,
            duration_ms=export.duration_ms,
            created_at=export.created_at.isoformat() if export.created_at else None,
            updated_at=export.updated_at.isoformat() if export.updated_at else None,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Kaspi feed get failed: export_id=%s company_id=%s error=%s", export_id, company_id, e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve feed",
        )


@router.get(
    "/feeds/{export_id}/payload",
    summary="Получить XML фида",
    response_class=Response,
)
async def kaspi_feed_get_payload(
    export_id: int,
    current_user: User = Depends(_auth_user),
    session: AsyncSession = Depends(get_async_db),
):
    """
    Возвращает XML payload фида с типом application/xml.
    """
    from app.models.kaspi_feed_export import KaspiFeedExport

    company_id = _resolve_company_id(current_user)

    try:
        stmt = sa.select(KaspiFeedExport).where(
            sa.and_(
                KaspiFeedExport.id == export_id,
                KaspiFeedExport.company_id == company_id,
            )
        )
        result = await session.execute(stmt)
        export = result.scalars().first()

        if not export:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Export not found",
            )

        return Response(
            content=export.payload_text,
            media_type="application/xml",
            headers={"Content-Disposition": f'attachment; filename="kaspi_feed_{export_id}.xml"'},
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Kaspi feed payload failed: export_id=%s company_id=%s error=%s", export_id, company_id, e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve payload",
        )


# ============================= STATUS (операционная панель) =============================


@router.get(
    "/status",
    summary="Статус интеграции Kaspi по компании",
    response_model=KaspiStatusOut,
)
async def kaspi_status(
    current_user: User = Depends(_auth_user),
    session: AsyncSession = Depends(get_async_db),
):
    """
    Возвращает срез состояния интеграции по компании: фиды, каталог, синк заказов, health.
    Без сетевых вызовов, только чтение из БД.
    """

    company_id = _resolve_company_id(current_user)

    try:
        company = (await session.execute(sa.select(Company).where(Company.id == company_id))).scalars().first()
        if not company:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Company not found")

        # Latest products feed
        feed_stmt = (
            sa.select(
                KaspiFeedExport.id,
                KaspiFeedExport.status,
                KaspiFeedExport.attempts,
                KaspiFeedExport.last_attempt_at,
                KaspiFeedExport.uploaded_at,
                KaspiFeedExport.duration_ms,
                KaspiFeedExport.last_error,
                KaspiFeedExport.created_at,
            )
            .where(
                sa.and_(
                    KaspiFeedExport.company_id == company_id,
                    KaspiFeedExport.kind == "products",
                )
            )
            .order_by(KaspiFeedExport.created_at.desc())
            .limit(1)
        )

        feed_row = await session.execute(feed_stmt)
        feed_row = feed_row.first()

        products_latest = None
        if feed_row:
            last_error = feed_row.last_error[:STATUS_LAST_ERROR_MAX_LEN] if feed_row.last_error else None
            products_latest = KaspiStatusFeedOut(
                id=feed_row.id,
                status=feed_row.status,
                attempts=feed_row.attempts or 0,
                last_attempt_at=feed_row.last_attempt_at.isoformat() if feed_row.last_attempt_at else None,
                uploaded_at=feed_row.uploaded_at.isoformat() if feed_row.uploaded_at else None,
                duration_ms=feed_row.duration_ms,
                last_error=last_error,
                created_at=feed_row.created_at.isoformat() if feed_row.created_at else None,
            )

        # Catalog aggregates
        catalog_stmt = (
            sa.select(
                sa.func.count(KaspiCatalogProduct.id),
                sa.func.count().filter(KaspiCatalogProduct.is_active.is_(True)),
                sa.func.max(KaspiCatalogProduct.updated_at),
            )
            .where(KaspiCatalogProduct.company_id == company_id)
            .limit(1)
        )
        catalog_row = await session.execute(catalog_stmt)
        catalog_row = catalog_row.first()
        catalog_total = catalog_row[0] or 0 if catalog_row else 0
        catalog_active = catalog_row[1] or 0 if catalog_row else 0
        catalog_last_updated = catalog_row[2].isoformat() if catalog_row and catalog_row[2] else None

        catalog = KaspiCatalogStatusOut(
            total=int(catalog_total),
            active=int(catalog_active),
            last_updated_at=catalog_last_updated,
        )

        # Orders sync state
        orders_sync_row = (
            (
                await session.execute(
                    sa.select(KaspiOrderSyncState).where(KaspiOrderSyncState.company_id == company_id).limit(1)
                )
            )
            .scalars()
            .first()
        )

        orders_sync = None
        if orders_sync_row:
            orders_sync = KaspiOrdersSyncStatusOut(
                last_synced_at=orders_sync_row.last_synced_at.isoformat() if orders_sync_row.last_synced_at else None,
                last_external_order_id=orders_sync_row.last_external_order_id,
                last_attempt_at=orders_sync_row.last_attempt_at.isoformat()
                if orders_sync_row.last_attempt_at
                else None,
                last_duration_ms=orders_sync_row.last_duration_ms,
                last_result=orders_sync_row.last_result,
                last_fetched=orders_sync_row.last_fetched,
                last_inserted=orders_sync_row.last_inserted,
                last_updated=orders_sync_row.last_updated,
                last_error_at=orders_sync_row.last_error_at.isoformat() if orders_sync_row.last_error_at else None,
                last_error_code=orders_sync_row.last_error_code,
                last_error_message=orders_sync_row.last_error_message,
                updated_at=orders_sync_row.updated_at.isoformat() if orders_sync_row.updated_at else None,
            )

        # Health: token presence (no secrets)
        has_token = False
        store_name = company.kaspi_store_id
        if store_name:
            token_count = await session.execute(
                sa.select(sa.func.count())
                .select_from(KaspiStoreToken)
                .where(sa.func.lower(KaspiStoreToken.store_name) == sa.func.lower(sa.literal(store_name)))
            )
            has_token = (token_count.scalar() or 0) > 0

        return KaspiStatusOut(
            feeds=KaspiStatusFeedsOut(products_latest=products_latest),
            catalog=catalog,
            orders_sync=orders_sync,
            health=KaspiHealthStatusOut(has_kaspi_token_configured=has_token),
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Kaspi status failed: company_id=%s error=%s", company_id, e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load Kaspi status",
        )
