from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession


class KaspiStatusFeedOut(BaseModel):
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


def register_kaspi_status_routes(
    router: APIRouter,
    *,
    auth_dependency: Any,
    get_async_db_dependency: Any,
    resolve_company_id_fn: Any,
    status_last_error_max_len: int,
    logger: Any,
    company_model: Any,
    kaspi_feed_export_model: Any,
    kaspi_catalog_product_model: Any,
    kaspi_order_sync_state_model: Any,
    kaspi_store_token_model: Any,
) -> None:
    async def kaspi_status(
        current_user: Any = Depends(auth_dependency),
        session: AsyncSession = Depends(get_async_db_dependency),
    ):
        company_id = resolve_company_id_fn(current_user)

        try:
            company = (
                (await session.execute(sa.select(company_model).where(company_model.id == company_id)))
                .scalars()
                .first()
            )
            if not company:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Company not found")

            feed_stmt = (
                sa.select(
                    kaspi_feed_export_model.id,
                    kaspi_feed_export_model.status,
                    kaspi_feed_export_model.attempts,
                    kaspi_feed_export_model.last_attempt_at,
                    kaspi_feed_export_model.uploaded_at,
                    kaspi_feed_export_model.duration_ms,
                    kaspi_feed_export_model.last_error,
                    kaspi_feed_export_model.created_at,
                )
                .where(
                    sa.and_(
                        kaspi_feed_export_model.company_id == company_id,
                        kaspi_feed_export_model.kind == "products",
                    )
                )
                .order_by(kaspi_feed_export_model.created_at.desc())
                .limit(1)
            )

            feed_row = await session.execute(feed_stmt)
            feed_row = feed_row.first()

            products_latest = None
            if feed_row:
                last_error = feed_row.last_error[:status_last_error_max_len] if feed_row.last_error else None
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

            catalog_stmt = (
                sa.select(
                    sa.func.count(kaspi_catalog_product_model.id),
                    sa.func.count().filter(kaspi_catalog_product_model.is_active.is_(True)),
                    sa.func.max(kaspi_catalog_product_model.updated_at),
                )
                .where(kaspi_catalog_product_model.company_id == company_id)
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

            orders_sync_row = (
                (
                    await session.execute(
                        sa.select(kaspi_order_sync_state_model)
                        .where(kaspi_order_sync_state_model.company_id == company_id)
                        .limit(1)
                    )
                )
                .scalars()
                .first()
            )

            orders_sync = None
            if orders_sync_row:
                orders_sync = KaspiOrdersSyncStatusOut(
                    last_synced_at=orders_sync_row.last_synced_at.isoformat()
                    if orders_sync_row.last_synced_at
                    else None,
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

            has_token = False
            store_name = company.kaspi_store_id
            if store_name:
                token_count = await session.execute(
                    sa.select(sa.func.count())
                    .select_from(kaspi_store_token_model)
                    .where(sa.func.lower(kaspi_store_token_model.store_name) == sa.func.lower(sa.literal(store_name)))
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

    router.add_api_route(
        "/status",
        kaspi_status,
        methods=["GET"],
        summary="Статус интеграции Kaspi по компании",
        response_model=KaspiStatusOut,
    )
