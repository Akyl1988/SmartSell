"""Kaspi feed export service: generation and upload pipeline."""

from __future__ import annotations

import hashlib
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

import httpx
import sqlalchemy as sa
from sqlalchemy import and_, or_, select, update
from sqlalchemy.dialects.postgresql import insert

from app.models import KaspiCatalogProduct, KaspiFeedExport
from app.services.kaspi_service import KaspiService

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


STALE_UPLOAD_TIMEOUT = timedelta(minutes=15)
MAX_ERROR_LENGTH = 1024


async def generate_products_feed(
    session: AsyncSession,
    company_id: int,
) -> dict:
    """
    Generate a Kaspi products feed export.

    1. Fetch all products for company_id from KaspiCatalogProduct
    2. Generate XML payload (deterministic by offer_id)
    3. Compute SHA256 checksum
    4. Check if export with same checksum exists (idempotency)
    5. If exists, return existing export; else insert new one with status="generated"
    6. Return summary {ok, export_id, company_id, total, active, checksum}
    """
    # Fetch products
    stmt = (
        select(KaspiCatalogProduct)
        .where(KaspiCatalogProduct.company_id == company_id)
        .order_by(KaspiCatalogProduct.offer_id)
    )

    result = await session.execute(stmt)
    products = result.scalars().all()

    # Generate XML
    root = ET.Element("products")
    active_count = 0

    for product in products:
        if product.is_active:
            active_count += 1

        prod_elem = ET.SubElement(root, "product")

        offer_id_elem = ET.SubElement(prod_elem, "offerId")
        offer_id_elem.text = product.offer_id

        if product.name:
            name_elem = ET.SubElement(prod_elem, "name")
            name_elem.text = product.name

        if product.sku:
            sku_elem = ET.SubElement(prod_elem, "sku")
            sku_elem.text = product.sku

        if product.price is not None:
            price_elem = ET.SubElement(prod_elem, "price")
            price_elem.text = str(product.price)

        if product.qty is not None:
            qty_elem = ET.SubElement(prod_elem, "quantity")
            qty_elem.text = str(product.qty)

        active_elem = ET.SubElement(prod_elem, "isActive")
        active_elem.text = "true" if product.is_active else "false"

    # Serialize XML
    payload_text = ET.tostring(root, encoding="unicode", method="xml")

    # Compute checksum
    checksum = hashlib.sha256(payload_text.encode("utf-8")).hexdigest()

    # Check if same export exists (idempotency)
    check_stmt = select(KaspiFeedExport).where(
        and_(
            KaspiFeedExport.company_id == company_id,
            KaspiFeedExport.kind == "products",
            KaspiFeedExport.checksum == checksum,
        )
    )

    check_result = await session.execute(check_stmt)
    existing = check_result.scalars().first()

    if existing:
        # Return existing export
        return {
            "ok": True,
            "export_id": existing.id,
            "company_id": company_id,
            "total": len(products),
            "active": active_count,
            "checksum": checksum,
            "is_new": False,
        }

    # Insert new export
    stats = {
        "total": len(products),
        "active": active_count,
    }

    stmt_insert = insert(KaspiFeedExport).values(
        company_id=company_id,
        kind="products",
        format="xml",
        status="generated",
        checksum=checksum,
        payload_text=payload_text,
        stats_json=stats,
    )

    result = await session.execute(stmt_insert)
    export_id = result.inserted_primary_key[0]
    await session.commit()

    return {
        "ok": True,
        "export_id": export_id,
        "company_id": company_id,
        "total": len(products),
        "active": active_count,
        "checksum": checksum,
        "is_new": True,
    }


def _classify_error(error: Exception) -> tuple[str, bool]:
    """
    Classify an error as retryable or not.

    Returns: (error_message, is_retryable)
    - Retryable: timeouts, network errors, 5xx server errors
    - Non-retryable: 4xx client errors, validation errors
    """
    error_str = str(error)
    error_type = type(error).__name__

    # Retryable errors
    if isinstance(error, httpx.TimeoutException | httpx.NetworkError | httpx.ConnectError):
        return f"{error_type}: {error_str}", True

    if isinstance(error, httpx.HTTPStatusError):
        status_code = error.response.status_code
        if 500 <= status_code < 600:
            return f"HTTP {status_code}: {error_str}", True
        elif 400 <= status_code < 500:
            return f"HTTP {status_code}: {error_str}", False

    # Default: treat unknown errors as non-retryable
    return f"{error_type}: {error_str}", False


async def upload_feed_export(
    session: AsyncSession,
    export_id: int,
    company_id: int,
    kaspi_service: KaspiService | None = None,
) -> dict:
    """
    Upload a feed export to Kaspi with concurrency protection and retry tracking.

    Concurrency is enforced via a short CAS update (no long-running transactions).
    Network upload is executed outside of any database transaction.

    Returns: {ok, export_id, status, error, is_retryable, already_uploaded, upload_in_progress}
    """
    if kaspi_service is None:
        kaspi_service = KaspiService()

    # Load export for initial checks/payload
    stmt = select(KaspiFeedExport).where(
        and_(
            KaspiFeedExport.id == export_id,
            KaspiFeedExport.company_id == company_id,
        )
    )

    result = await session.execute(stmt)
    export = result.scalars().first()

    if not export:
        return {
            "ok": False,
            "export_id": export_id,
            "status": None,
            "error": "Export not found or access denied",
            "is_retryable": False,
            "already_uploaded": False,
            "upload_in_progress": False,
        }

    if export.status == "uploaded":
        return {
            "ok": True,
            "export_id": export_id,
            "status": "uploaded",
            "error": None,
            "is_retryable": False,
            "already_uploaded": True,
            "upload_in_progress": False,
        }

    now = datetime.utcnow()
    stale_cutoff = now - STALE_UPLOAD_TIMEOUT

    # Short transaction: attempt to claim the upload slot via CAS update
    claim_stmt = (
        update(KaspiFeedExport)
        .where(
            and_(
                KaspiFeedExport.id == export_id,
                KaspiFeedExport.company_id == company_id,
                or_(
                    KaspiFeedExport.status.in_(("generated", "failed")),
                    and_(
                        KaspiFeedExport.status == "uploading",
                        or_(
                            KaspiFeedExport.last_attempt_at.is_(None),
                            KaspiFeedExport.last_attempt_at < stale_cutoff,
                        ),
                    ),
                ),
            )
        )
        .values(
            status="uploading",
            attempts=sa.func.coalesce(KaspiFeedExport.attempts, 0) + 1,
            last_attempt_at=now,
            last_error=None,
            updated_at=now,
        )
        .returning(
            KaspiFeedExport.attempts,
            KaspiFeedExport.payload_text,
        )
    )

    claim_result = await session.execute(claim_stmt)
    claimed_row = claim_result.first()
    await session.commit()

    if not claimed_row:
        reread_stmt = (
            select(
                KaspiFeedExport.status,
                KaspiFeedExport.last_attempt_at,
                KaspiFeedExport.attempts,
                KaspiFeedExport.uploaded_at,
            )
            .where(
                and_(
                    KaspiFeedExport.id == export_id,
                    KaspiFeedExport.company_id == company_id,
                )
            )
            .limit(1)
        )
        reread_result = await session.execute(reread_stmt)
        current = reread_result.first()

        if current and current.status == "uploaded":
            return {
                "ok": True,
                "export_id": export_id,
                "status": "uploaded",
                "error": None,
                "is_retryable": False,
                "already_uploaded": True,
                "upload_in_progress": False,
            }

        if (
            current
            and current.status == "uploading"
            and current.last_attempt_at
            and current.last_attempt_at >= stale_cutoff
        ):
            return {
                "ok": False,
                "export_id": export_id,
                "status": current.status,
                "error": "Upload already in progress",
                "is_retryable": True,
                "already_uploaded": False,
                "upload_in_progress": True,
            }

        return {
            "ok": False,
            "export_id": export_id,
            "status": current.status if current else export.status,
            "error": "Upload not claimable",
            "is_retryable": True,
            "already_uploaded": False,
            "upload_in_progress": False,
        }

    payload_text = claimed_row.payload_text

    start_time = time.perf_counter()

    try:
        await kaspi_service.upload_products_feed(payload_text)

        duration_ms = max(1, int((time.perf_counter() - start_time) * 1000))
        now_success = datetime.utcnow()

        await session.execute(
            update(KaspiFeedExport)
            .where(
                and_(
                    KaspiFeedExport.id == export_id,
                    KaspiFeedExport.company_id == company_id,
                )
            )
            .values(
                status="uploaded",
                uploaded_at=now_success,
                duration_ms=duration_ms,
                updated_at=now_success,
            )
        )
        await session.commit()

        return {
            "ok": True,
            "export_id": export_id,
            "status": "uploaded",
            "error": None,
            "is_retryable": False,
            "already_uploaded": False,
            "upload_in_progress": False,
        }

    except Exception as e:
        duration_ms = max(1, int((time.perf_counter() - start_time) * 1000))
        error_message, is_retryable = _classify_error(e)
        error_message = error_message[:MAX_ERROR_LENGTH]
        now_fail = datetime.utcnow()

        await session.execute(
            update(KaspiFeedExport)
            .where(
                and_(
                    KaspiFeedExport.id == export_id,
                    KaspiFeedExport.company_id == company_id,
                )
            )
            .values(
                status="failed",
                last_error=error_message,
                duration_ms=duration_ms,
                updated_at=now_fail,
            )
        )
        await session.commit()

        return {
            "ok": False,
            "export_id": export_id,
            "status": "failed",
            "error": error_message,
            "is_retryable": is_retryable,
            "already_uploaded": False,
            "upload_in_progress": False,
        }
