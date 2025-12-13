# -*- coding: utf-8 -*-
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
- GET    /api/v1/kaspi/feed/{company_id}      — сгенерировать XML-фид активных товаров компании.
- POST   /api/v1/kaspi/availability/sync      — синхронизировать доступность одного товара.
- POST   /api/v1/kaspi/availability/bulk      — массовая синхронизация доступности по компании.
- GET    /api/v1/kaspi/_debug/ping            — диагностический ping.

Принципы:
- Предсказуемые ответы: 4xx/5xx с внятным detail, без «просто 500».
- Pydantic v2, SQLAlchemy 2.x (AsyncSession).
- Безопасность: наружу не отдаём сырые токены, только маска + метаданные.
- Расширяемость: аккуратные модели ввода/вывода; готово к будущим эндпоинтам.
"""

import logging
from typing import Any

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import text, bindparam
from sqlalchemy.ext.asyncio import AsyncSession

# БД (единый вход, обратная совместимость):
from app.core.db import get_async_db as get_db
from app.core.db import get_async_db  # noqa — для совместимости импорт-алиас

# Доменные зависимости/схемы:
from app.integrations.kaspi_adapter import KaspiAdapter, KaspiAdapterError
from app.models import Product
from app.models.kaspi_token import KaspiStoreToken
from app.schemas.kaspi import (
    KaspiTokenIn,
    KaspiTokenOut,
    OrdersQuery,
    ImportRequest,
    ImportStatusQuery,
)
from app.services.kaspi_service import KaspiService

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/kaspi", tags=["kaspi"])


# ----------------------------- Константы/утилиты -----------------------------

MASK_HEX_LEN = 10
MASK_CHAR = "..."


def normalize_name(name: str) -> str:
    return name.strip().lower()


# ------------------------------- Локальные схемы -----------------------------

class ConnectStoreInput(BaseModel):
    store_name: str = Field(..., min_length=3, description="Имя магазина (уникальное)")
    token: str = Field(..., min_length=8, description="API token для Kaspi API")
    verify: bool = Field(True, description="Проверить токен запросом к Kaspi")
    save: bool = Field(True, description="Сохранить токен в БД при успехе")

    @field_validator("store_name")
    @classmethod
    def _strip_name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("store_name is empty")
        return v


class ConnectStoreOut(BaseModel):
    ok: bool
    store_name: str
    verified: bool
    saved: bool
    message: str | None = None
    adapter_health: Any | None = None


class OrdersSyncIn(BaseModel):
    company_id: int = Field(..., ge=1, description="ID компании, для которой синхронизируем заказы")


class AvailabilitySyncIn(BaseModel):
    product_id: int = Field(..., ge=1, description="ID продукта в нашей БД")


class AvailabilityBulkIn(BaseModel):
    company_id: int = Field(..., ge=1)
    limit: int = Field(500, ge=1, le=5000, description="Максимум товаров за одну операцию")


class KaspiTokenMaskedOut(BaseModel):
    """Ответ для карточки токена без раскрытия секрета."""
    id: str
    store_name: str
    token_hex_masked: str
    created_at: Any
    updated_at: Any


# ================================= CONNECT ===================================

@router.post(
    "/connect",
    response_model=ConnectStoreOut,
    status_code=status.HTTP_200_OK,
    summary="Подключить магазин Kaspi (verify/save)",
)
async def connect_store(
    body: ConnectStoreInput,
    session: AsyncSession = Depends(get_db),
):
    """
    1) (опц.) Проверяем токен/магазин (через адаптер, health).
    2) (опц.) Сохраняем токен.
    """
    verified = False
    saved = False
    health_payload: Any | None = None

    # 1) verify
    if body.verify:
        try:
            # На этапе verify — проверяем доступность профиля магазина.
            health_payload = KaspiAdapter().health(body.store_name)
            verified = True
        except KaspiAdapterError as e:
            logger.warning("Kaspi connect verify failed: store=%s err=%s", body.store_name, e)
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"verification_failed: {e}",
            )

    # 2) save token
    if body.save:
        try:
            await KaspiStoreToken.upsert_token(session, body.store_name, body.token)
            saved = True
        except Exception as e:
            logger.error("Kaspi connect save token failed: store=%s err=%s", body.store_name, e)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"save_failed: {e}",
            )

    return ConnectStoreOut(
        ok=True,
        store_name=body.store_name,
        verified=verified or not body.verify,
        saved=saved,
        adapter_health=health_payload,
        message="connected",
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
    session: AsyncSession = Depends(get_db),
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
async def list_tokens(session: AsyncSession = Depends(get_db)):
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
    session: AsyncSession = Depends(get_db),
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
async def delete_token(store_name: str, session: AsyncSession = Depends(get_db)):
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
    payload: OrdersSyncIn,
    session: AsyncSession = Depends(get_db),
):
    try:
        svc = KaspiService()
        result = await svc.sync_orders(company_id=payload.company_id, db=session)
        return result
    except RuntimeError as e:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(e))
    except Exception as e:
        logger.error("Kaspi orders sync failed: payload=%s err=%s", payload.model_dump(), e)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))


@router.get(
    "/feed/{company_id}",
    summary="Сгенерировать XML-фид активных товаров компании",
    response_class=Response,
)
async def kaspi_generate_feed(
    company_id: int,
    session: AsyncSession = Depends(get_db),
):
    try:
        svc = KaspiService()
        xml_body = await svc.generate_product_feed(company_id=company_id, db=session)
        return Response(content=xml_body, media_type="application/xml")
    except RuntimeError as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))
    except Exception as e:
        logger.error("Kaspi generate feed unexpected error: company_id=%s err=%s", company_id, e)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))


@router.post(
    "/availability/sync",
    summary="Синхронизировать доступность (stock) одного товара в Kaspi",
)
async def kaspi_availability_sync_one(
    payload: AvailabilitySyncIn,
    session: AsyncSession = Depends(get_db),
):
    try:
        res = await session.execute(sa.select(Product).where(Product.id == payload.product_id))
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
    session: AsyncSession = Depends(get_db),
):
    try:
        svc = KaspiService()
        stats = await svc.bulk_sync_availability(
            company_id=payload.company_id, db=session, limit=payload.limit
        )
        return stats
    except Exception as e:
        logger.error("Kaspi availability bulk failed: payload=%s err=%s", payload.model_dump(), e)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(e))


# ================================= DEBUG =====================================

@router.get("/_debug/ping", summary="Kaspi debug ping")
def kaspi_debug_ping():
    return {"ok": True, "module": "kaspi", "prefix": router.prefix}
