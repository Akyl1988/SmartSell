"""Tests for Kaspi feed export MVP."""

from __future__ import annotations

import pytest
import sqlalchemy as sa
from sqlalchemy import select

from app.models.kaspi_catalog_product import KaspiCatalogProduct
from app.models.kaspi_feed_export import KaspiFeedExport
from app.services.kaspi_service import KaspiService


@pytest.mark.asyncio
async def test_generate_creates_export_and_payload_contains_offers(
    monkeypatch,
    async_db_session,
):
    """Test that generate_products_feed creates export with valid XML payload."""
    from app.models.company import Company
    from app.services.kaspi_feed_export_service import generate_products_feed

    # Create a test company and products
    company = Company(name="TestCo A")
    async_db_session.add(company)
    await async_db_session.flush()

    products = [
        KaspiCatalogProduct(
            company_id=company.id,
            offer_id="OFFER001",
            name="Product A",
            sku="SKU-A",
            price=100.50,
            qty=10,
            is_active=True,
        ),
        KaspiCatalogProduct(
            company_id=company.id,
            offer_id="OFFER002",
            name="Product B",
            sku="SKU-B",
            price=200.75,
            qty=0,
            is_active=False,
        ),
    ]
    for p in products:
        async_db_session.add(p)
    await async_db_session.commit()

    result = await generate_products_feed(async_db_session, company.id)

    assert result["ok"] is True
    assert result["export_id"] is not None
    assert result["company_id"] == company.id
    assert result["total"] == 2
    assert result["active"] == 1
    assert result["is_new"] is True

    # Verify export in DB
    stmt = select(KaspiFeedExport).where(KaspiFeedExport.id == result["export_id"])
    db_result = await async_db_session.execute(stmt)
    export = db_result.scalars().first()

    assert export is not None
    assert export.status == "generated"
    assert export.kind == "products"
    assert export.format == "xml"
    assert export.checksum == result["checksum"]
    assert export.stats_json == {"total": 2, "active": 1}

    # Verify XML payload structure
    assert "OFFER001" in export.payload_text
    assert "Product A" in export.payload_text
    assert "OFFER002" in export.payload_text


@pytest.mark.asyncio
async def test_generate_idempotent_same_checksum_returns_existing_export(
    async_db_session,
):
    """Test that generate_products_feed returns existing export if checksum matches."""
    from app.models.company import Company
    from app.services.kaspi_feed_export_service import generate_products_feed

    company = Company(name="TestCo B")
    async_db_session.add(company)
    await async_db_session.flush()

    products = [
        KaspiCatalogProduct(
            company_id=company.id,
            offer_id="OFFER001",
            name="Product A",
            sku="SKU-A",
            price=100.50,
            qty=10,
            is_active=True,
        ),
        KaspiCatalogProduct(
            company_id=company.id,
            offer_id="OFFER002",
            name="Product B",
            sku="SKU-B",
            price=200.75,
            qty=0,
            is_active=False,
        ),
    ]
    for p in products:
        async_db_session.add(p)
    await async_db_session.commit()

    # First generation
    result1 = await generate_products_feed(async_db_session, company.id)
    export_id_1 = result1["export_id"]
    checksum_1 = result1["checksum"]

    # Count exports
    count_stmt = select(KaspiFeedExport).where(KaspiFeedExport.company_id == company.id)
    count_result = await async_db_session.execute(count_stmt)
    exports_1 = count_result.scalars().all()
    count_1 = len(exports_1)

    # Second generation (should return same)
    result2 = await generate_products_feed(async_db_session, company.id)
    export_id_2 = result2["export_id"]
    checksum_2 = result2["checksum"]

    assert export_id_1 == export_id_2
    assert checksum_1 == checksum_2
    assert result2["is_new"] is False

    # Count exports again (should not increase)
    count_result = await async_db_session.execute(count_stmt)
    exports_2 = count_result.scalars().all()
    count_2 = len(exports_2)

    assert count_1 == count_2  # No new export created


@pytest.mark.asyncio
async def test_tenant_isolation_exports_separate(
    async_db_session,
):
    """Test that company A exports don't leak to company B."""
    from app.models.company import Company
    from app.services.kaspi_feed_export_service import generate_products_feed

    company_a = Company(name="TestCo A")
    company_b = Company(name="TestCo B")
    async_db_session.add(company_a)
    async_db_session.add(company_b)
    await async_db_session.flush()

    # Products for company A
    for offer_id in ["OFFER001", "OFFER002"]:
        p = KaspiCatalogProduct(
            company_id=company_a.id,
            offer_id=offer_id,
            name=f"Product {offer_id}",
            sku=f"SKU-{offer_id}",
            price=100.00,
            qty=10,
            is_active=True,
        )
        async_db_session.add(p)

    # Products for company B
    for offer_id in ["OFFER003", "OFFER004"]:
        p = KaspiCatalogProduct(
            company_id=company_b.id,
            offer_id=offer_id,
            name=f"Product {offer_id}",
            sku=f"SKU-{offer_id}",
            price=200.00,
            qty=20,
            is_active=True,
        )
        async_db_session.add(p)

    await async_db_session.commit()

    # Generate for company A
    result_a = await generate_products_feed(async_db_session, company_a.id)
    assert result_a["total"] == 2

    # Generate for company B
    result_b = await generate_products_feed(async_db_session, company_b.id)
    assert result_b["total"] == 2

    # List exports for A (should only see A's export)
    stmt_a = select(KaspiFeedExport).where(KaspiFeedExport.company_id == company_a.id)
    result_a_list = await async_db_session.execute(stmt_a)
    exports_a = result_a_list.scalars().all()
    assert len(exports_a) == 1
    assert exports_a[0].id == result_a["export_id"]

    # List exports for B (should only see B's export)
    stmt_b = select(KaspiFeedExport).where(KaspiFeedExport.company_id == company_b.id)
    result_b_list = await async_db_session.execute(stmt_b)
    exports_b = result_b_list.scalars().all()
    assert len(exports_b) == 1
    assert exports_b[0].id == result_b["export_id"]

    # Checksums should differ (different products)
    assert result_a["checksum"] != result_b["checksum"]


@pytest.mark.asyncio
async def test_upload_sets_status_uploaded_on_mock_success(
    monkeypatch,
    async_db_session,
):
    """Test that upload_feed_export sets status=uploaded on successful upload."""
    from app.models.company import Company
    from app.services.kaspi_feed_export_service import generate_products_feed, upload_feed_export

    company = Company(name="TestCo C")
    async_db_session.add(company)
    await async_db_session.flush()

    # Create a product
    p = KaspiCatalogProduct(
        company_id=company.id,
        offer_id="OFFER001",
        name="Product A",
        sku="SKU-A",
        price=100.00,
        qty=10,
        is_active=True,
    )
    async_db_session.add(p)
    await async_db_session.commit()

    # Generate export
    gen_result = await generate_products_feed(async_db_session, company.id)
    export_id = gen_result["export_id"]

    # Mock successful upload
    async def mock_upload_success(self, xml_payload: str) -> bool:
        assert isinstance(xml_payload, str)
        assert len(xml_payload) > 0
        return True

    monkeypatch.setattr(KaspiService, "upload_products_feed", mock_upload_success)

    # Upload
    upload_result = await upload_feed_export(
        async_db_session,
        export_id,
        company.id,
        kaspi_service=KaspiService(),
    )

    assert upload_result["ok"] is True
    assert upload_result["status"] == "uploaded"
    assert upload_result["error"] is None

    # Verify in DB
    stmt = select(KaspiFeedExport).where(KaspiFeedExport.id == export_id)
    db_result = await async_db_session.execute(stmt)
    export = db_result.scalars().first()
    assert export.status == "uploaded"
    assert export.last_error is None


@pytest.mark.asyncio
async def test_upload_sets_status_failed_on_mock_error(
    monkeypatch,
    async_db_session,
):
    """Test that upload_feed_export sets status=failed and records error on failure."""
    from app.models.company import Company
    from app.services.kaspi_feed_export_service import generate_products_feed, upload_feed_export

    company = Company(name="TestCo D")
    async_db_session.add(company)
    await async_db_session.flush()

    # Create a product
    p = KaspiCatalogProduct(
        company_id=company.id,
        offer_id="OFFER001",
        name="Product A",
        sku="SKU-A",
        price=100.00,
        qty=10,
        is_active=True,
    )
    async_db_session.add(p)
    await async_db_session.commit()

    # Generate export
    gen_result = await generate_products_feed(async_db_session, company.id)
    export_id = gen_result["export_id"]

    # Mock failed upload
    async def mock_upload_failure(self, xml_payload: str) -> bool:
        raise RuntimeError("Network error: connection timeout")

    monkeypatch.setattr(KaspiService, "upload_products_feed", mock_upload_failure)

    # Upload
    upload_result = await upload_feed_export(
        async_db_session,
        export_id,
        company.id,
        kaspi_service=KaspiService(),
    )

    assert upload_result["ok"] is False
    assert upload_result["status"] == "failed"
    assert upload_result["error"] is not None
    assert "Network error" in upload_result["error"]

    # Verify in DB
    stmt = select(KaspiFeedExport).where(KaspiFeedExport.id == export_id)
    db_result = await async_db_session.execute(stmt)
    export = db_result.scalars().first()
    assert export.status == "failed"
    assert "Network error" in export.last_error


# ========================================
# Hardening Tests
# ========================================


@pytest.mark.asyncio
async def test_upload_idempotent_when_already_uploaded(
    monkeypatch,
    async_db_session,
):
    """Test that uploading an already-uploaded export returns early without re-uploading."""
    from app.models.company import Company
    from app.services.kaspi_feed_export_service import generate_products_feed, upload_feed_export

    # Create company and product
    company = Company(name="TestCo")
    async_db_session.add(company)
    await async_db_session.flush()

    p = KaspiCatalogProduct(
        company_id=company.id,
        offer_id="OFFER001",
        name="Product A",
        sku="SKU-A",
        price=100.00,
        qty=10,
        is_active=True,
    )
    async_db_session.add(p)
    await async_db_session.commit()

    # Generate export
    gen_result = await generate_products_feed(async_db_session, company.id)
    export_id = gen_result["export_id"]

    # Mock successful upload
    upload_count = 0

    async def mock_upload_success(self, xml_payload: str) -> bool:
        nonlocal upload_count
        upload_count += 1
        return True

    monkeypatch.setattr(KaspiService, "upload_products_feed", mock_upload_success)

    # First upload
    result1 = await upload_feed_export(
        async_db_session,
        export_id,
        company.id,
        kaspi_service=KaspiService(),
    )

    assert result1["ok"] is True
    assert result1["status"] == "uploaded"
    assert result1["already_uploaded"] is False
    assert upload_count == 1

    # Second upload (should be idempotent)
    result2 = await upload_feed_export(
        async_db_session,
        export_id,
        company.id,
        kaspi_service=KaspiService(),
    )

    assert result2["ok"] is True
    assert result2["status"] == "uploaded"
    assert result2["already_uploaded"] is True  # key assertion
    assert upload_count == 1  # no second upload


@pytest.mark.asyncio
async def test_upload_increments_attempts_and_sets_timestamps(
    monkeypatch,
    async_db_session,
):
    """Test that upload tracks attempts, last_attempt_at, and duration_ms."""
    from app.models.company import Company
    from app.services.kaspi_feed_export_service import generate_products_feed, upload_feed_export

    company = Company(name="TestCo")
    async_db_session.add(company)
    await async_db_session.flush()

    p = KaspiCatalogProduct(
        company_id=company.id,
        offer_id="OFFER001",
        name="Product A",
        sku="SKU-A",
        price=100.00,
        qty=10,
        is_active=True,
    )
    async_db_session.add(p)
    await async_db_session.commit()

    # Generate export
    gen_result = await generate_products_feed(async_db_session, company.id)
    export_id = gen_result["export_id"]

    # Mock successful upload
    async def mock_upload_success(self, xml_payload: str) -> bool:
        return True

    monkeypatch.setattr(KaspiService, "upload_products_feed", mock_upload_success)

    # Upload
    upload_result = await upload_feed_export(
        async_db_session,
        export_id,
        company.id,
        kaspi_service=KaspiService(),
    )

    assert upload_result["ok"] is True
    assert upload_result["status"] == "uploaded"

    # Verify DB fields
    stmt = select(KaspiFeedExport).where(KaspiFeedExport.id == export_id)
    db_result = await async_db_session.execute(stmt)
    export = db_result.scalars().first()

    assert export.attempts == 1
    assert export.last_attempt_at is not None
    assert export.uploaded_at is not None
    assert export.duration_ms is not None
    assert export.duration_ms > 0


@pytest.mark.asyncio
async def test_upload_classifies_retryable_errors(
    monkeypatch,
    async_db_session,
):
    """Test that network/timeout/5xx errors are classified as retryable."""
    import httpx

    from app.models.company import Company
    from app.services.kaspi_feed_export_service import generate_products_feed, upload_feed_export

    company = Company(name="TestCo")
    async_db_session.add(company)
    await async_db_session.flush()

    p = KaspiCatalogProduct(
        company_id=company.id,
        offer_id="OFFER001",
        name="Product A",
        sku="SKU-A",
        price=100.00,
        qty=10,
        is_active=True,
    )
    async_db_session.add(p)
    await async_db_session.commit()

    # Generate export
    gen_result = await generate_products_feed(async_db_session, company.id)
    export_id = gen_result["export_id"]

    # Test 1: Timeout error (retryable)
    async def mock_timeout_error(self, xml_payload: str) -> bool:
        raise httpx.TimeoutException("Request timeout")

    monkeypatch.setattr(KaspiService, "upload_products_feed", mock_timeout_error)

    result = await upload_feed_export(
        async_db_session,
        export_id,
        company.id,
        kaspi_service=KaspiService(),
    )

    assert result["ok"] is False
    assert result["is_retryable"] is True  # key assertion
    assert "TimeoutException" in result["error"]

    # Test 2: 503 Service Unavailable (retryable)
    async def mock_5xx_error(self, xml_payload: str) -> bool:
        # Create a mock response
        mock_request = httpx.Request("POST", "https://api.kaspi.kz")
        mock_response = httpx.Response(503, request=mock_request)
        raise httpx.HTTPStatusError("Service unavailable", request=mock_request, response=mock_response)

    monkeypatch.setattr(KaspiService, "upload_products_feed", mock_5xx_error)

    result = await upload_feed_export(
        async_db_session,
        export_id,
        company.id,
        kaspi_service=KaspiService(),
    )

    assert result["ok"] is False
    assert result["is_retryable"] is True  # 5xx is retryable


@pytest.mark.asyncio
async def test_upload_classifies_non_retryable_errors(
    monkeypatch,
    async_db_session,
):
    """Test that 4xx client errors are classified as non-retryable."""
    import httpx

    from app.models.company import Company
    from app.services.kaspi_feed_export_service import generate_products_feed, upload_feed_export

    company = Company(name="TestCo")
    async_db_session.add(company)
    await async_db_session.flush()

    p = KaspiCatalogProduct(
        company_id=company.id,
        offer_id="OFFER001",
        name="Product A",
        sku="SKU-A",
        price=100.00,
        qty=10,
        is_active=True,
    )
    async_db_session.add(p)
    await async_db_session.commit()

    # Generate export
    gen_result = await generate_products_feed(async_db_session, company.id)
    export_id = gen_result["export_id"]

    # Mock 400 Bad Request (non-retryable)
    async def mock_4xx_error(self, xml_payload: str) -> bool:
        mock_request = httpx.Request("POST", "https://api.kaspi.kz")
        mock_response = httpx.Response(400, request=mock_request)
        raise httpx.HTTPStatusError("Bad request", request=mock_request, response=mock_response)

    monkeypatch.setattr(KaspiService, "upload_products_feed", mock_4xx_error)

    result = await upload_feed_export(
        async_db_session,
        export_id,
        company.id,
        kaspi_service=KaspiService(),
    )

    assert result["ok"] is False
    assert result["is_retryable"] is False  # 4xx is non-retryable
    assert "HTTP 400" in result["error"]


@pytest.mark.asyncio
async def test_concurrent_upload_protection_with_advisory_lock(
    monkeypatch,
    async_db_session,
):
    """
    Test that concurrent uploads are prevented via advisory lock.

    This test simulates the lock being busy by mocking the pg_try_advisory_xact_lock result.
    """
    from unittest.mock import AsyncMock

    from sqlalchemy.engine import Result

    from app.models.company import Company
    from app.services.kaspi_feed_export_service import generate_products_feed, upload_feed_export

    company = Company(name="TestCo")
    async_db_session.add(company)
    await async_db_session.flush()

    p = KaspiCatalogProduct(
        company_id=company.id,
        offer_id="OFFER001",
        name="Product A",
        sku="SKU-A",
        price=100.00,
        qty=10,
        is_active=True,
    )
    async_db_session.add(p)
    await async_db_session.commit()

    # Generate export
    gen_result = await generate_products_feed(async_db_session, company.id)
    export_id = gen_result["export_id"]

    # Mock the session.execute to return False for advisory lock (lock busy)
    original_execute = async_db_session.execute

    async def mock_execute(stmt, *args, **kwargs):
        # Check if this is the advisory lock query
        if hasattr(stmt, "text") and "pg_try_advisory_xact_lock" in str(stmt):
            # Return False to simulate lock busy
            mock_result = AsyncMock(spec=Result)
            mock_result.scalar.return_value = False
            return mock_result
        # Otherwise, call the original
        return await original_execute(stmt, *args, **kwargs)

    monkeypatch.setattr(async_db_session, "execute", mock_execute)

    # Attempt upload (should fail due to lock busy)
    upload_result = await upload_feed_export(
        async_db_session,
        export_id,
        company.id,
        kaspi_service=KaspiService(),
    )

    assert upload_result["ok"] is False
    assert upload_result["upload_in_progress"] is True  # key assertion
    assert "already in progress" in upload_result["error"].lower()
    assert upload_result["is_retryable"] is True
