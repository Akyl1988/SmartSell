from __future__ import annotations

"""
Kaspi catalog products synchronization service.

Fetches product catalog from Kaspi API and syncs to local database with idempotent upsert.
Tenant-scoped: each company has isolated catalog.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import get_logger
from app.models.company import Company
from app.models.kaspi_catalog_product import KaspiCatalogProduct
from app.models.marketplace import KaspiStoreToken
from app.services.kaspi_service import KaspiProductsUpstreamError, KaspiService

logger = get_logger(__name__)


async def sync_kaspi_catalog_products(
    session: AsyncSession,
    company_id: int,
    kaspi: KaspiService | None = None,
    *,
    page_size: int = 100,
    max_pages: int = 100,
    request_id: str | None = None,
) -> dict[str, Any]:
    """
    Synchronize Kaspi catalog products to local database.

    Args:
        session: Async database session
        company_id: Company ID (tenant isolation)
        kaspi: KaspiService instance (if None, creates new one)

    Returns:
        Summary dict with keys: ok, company_id, fetched, inserted, updated
    """
    res_company = await session.execute(select(Company).where(Company.id == company_id))
    company = res_company.scalars().first()
    if not company:
        raise ValueError("company_not_found")

    store_name = (company.kaspi_store_id or "").strip()
    if not store_name:
        raise ValueError("kaspi_store_not_configured")

    token = await KaspiStoreToken.get_token(session, store_name)
    if not token:
        raise ValueError("kaspi_token_not_found")

    if kaspi is None:
        kaspi = KaspiService(api_key=token, base_url=settings.KASPI_API_URL)

    fetched = 0
    inserted = 0
    updated = 0

    logger.info("Kaspi catalog sync start: company_id=%s", company_id)

    try:
        # Fetch products from Kaspi API with pagination
        for page in range(1, max_pages + 1):
            items = await kaspi.get_products(
                page=page,
                page_size=page_size,
                company_id=company_id,
                store_name=store_name,
                request_id=request_id,
            )
            if not items:
                break

            fetched += len(items)

            # Process each product item
            for item in items:
                # Extract offer_id (priority: offer_id, then id)
                offer_id = item.get("offer_id") or item.get("id")
                if not offer_id:
                    logger.warning("Kaspi catalog sync: skipping item without offer_id/id")
                    continue

                # Check if product already exists
                check_stmt = select(KaspiCatalogProduct.id).where(
                    KaspiCatalogProduct.company_id == company_id,
                    KaspiCatalogProduct.offer_id == str(offer_id),
                )
                result_check = await session.execute(check_stmt)
                exists = result_check.scalar_one_or_none()

                # Extract fields
                name = item.get("name") or item.get("title")
                sku = item.get("sku") or item.get("code")
                price = item.get("price")
                qty = item.get("qty") or item.get("quantity") or item.get("stock")
                is_active = item.get("is_active", True)

                # Build upsert statement (ON CONFLICT DO UPDATE)
                stmt = (
                    insert(KaspiCatalogProduct)
                    .values(
                        company_id=company_id,
                        offer_id=str(offer_id),
                        name=str(name) if name else None,
                        sku=str(sku) if sku else None,
                        price=float(price) if price is not None else None,
                        qty=int(qty) if qty is not None else None,
                        is_active=bool(is_active),
                        raw=item,
                        created_at=datetime.utcnow(),
                        updated_at=datetime.utcnow(),
                    )
                    .on_conflict_do_update(
                        index_elements=["company_id", "offer_id"],
                        set_={
                            "name": str(name) if name else None,
                            "sku": str(sku) if sku else None,
                            "price": float(price) if price is not None else None,
                            "qty": int(qty) if qty is not None else None,
                            "is_active": bool(is_active),
                            "raw": item,
                            "updated_at": datetime.utcnow(),
                        },
                    )
                )

                await session.execute(stmt)

                # Track insert vs update
                if exists:
                    updated += 1
                else:
                    inserted += 1

            if len(items) < page_size:
                break

        # Commit transaction
        await session.commit()

        logger.info(
            "Kaspi catalog sync success: company_id=%s fetched=%s inserted=%s updated=%s",
            company_id,
            fetched,
            inserted,
            updated,
        )

        return {
            "ok": True,
            "company_id": company_id,
            "fetched": fetched,
            "inserted": inserted,
            "updated": updated,
        }

    except KaspiProductsUpstreamError as e:
        logger.error(
            "Kaspi catalog sync failed: company_id=%s store=%s error_code=%s error=%s",
            company_id,
            store_name,
            e.code,
            repr(e),
        )
        await session.rollback()
        raise
    except Exception as e:
        logger.error("Kaspi catalog sync failed: company_id=%s error=%s", company_id, e)
        await session.rollback()
        raise
