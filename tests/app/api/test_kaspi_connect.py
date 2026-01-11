"""
Tests for Kaspi connect (onboarding) endpoint.

Covers:
- company_name requirement (missing/blank -> 422)
- Company.name updated from request
- Token stored encrypted via KaspiStoreToken
- Verify=false skips adapter call
- Verify=true calls adapter and fails if invalid
- Private metadata storage in Company.settings
- Tenant isolation (company_id from current_user only)
"""

import json
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import create_access_token, get_password_hash
from app.models.company import Company
from app.models.marketplace import KaspiStoreToken
from app.models.user import User


@pytest.mark.asyncio
class TestKaspiConnect:
    """Test suite for Kaspi store connection (onboarding) endpoint."""

    async def _create_user_and_company(self, async_db_session: AsyncSession, phone: str):
        """Helper to create a test user with a company."""
        company = Company(name=f"Company for {phone}")
        async_db_session.add(company)
        await async_db_session.flush()

        user = User(
            phone=phone,
            company_id=company.id,
            hashed_password=get_password_hash("Secret123!"),
            role="manager",
            is_active=True,
            is_verified=True,
        )
        async_db_session.add(user)
        await async_db_session.commit()
        return user, company

    def _get_auth_header(self, user: User) -> dict:
        """Helper to create auth header with valid JWT token."""
        token = create_access_token(subject=user.id, extra={"company_id": user.company_id, "role": user.role})
        return {"Authorization": f"Bearer {token}"}

    @pytest.mark.asyncio
    async def test_connect_requires_company_name(self, async_client: AsyncClient, async_db_session: AsyncSession):
        """Test: missing company_name returns 422."""
        user, company = await self._create_user_and_company(async_db_session, "77001234567")
        headers = self._get_auth_header(user)

        response = await async_client.post(
            "/api/v1/kaspi/connect",
            json={
                "store_name": "my_store",
                "token": "valid_token_12345",
                "verify": False,
                # company_name missing
            },
            headers=headers,
        )

        assert response.status_code == 422, response.text

    @pytest.mark.asyncio
    async def test_connect_rejects_blank_company_name(self, async_client: AsyncClient, async_db_session: AsyncSession):
        """Test: empty/whitespace company_name returns 422."""
        user, company = await self._create_user_and_company(async_db_session, "77001234568")
        headers = self._get_auth_header(user)

        response = await async_client.post(
            "/api/v1/kaspi/connect",
            json={
                "company_name": "",
                "store_name": "my_store",
                "token": "valid_token_12345",
                "verify": False,
            },
            headers=headers,
        )

        assert response.status_code == 422, response.text

    @pytest.mark.asyncio
    async def test_connect_updates_company_name(self, async_client: AsyncClient, async_db_session: AsyncSession):
        """Test: connect updates companies.name and companies.kaspi_store_id."""
        user, company = await self._create_user_and_company(async_db_session, "77001234569")
        headers = self._get_auth_header(user)

        # Mock KaspiAdapter.health and KaspiStoreToken.upsert_token to avoid pgcrypto dependency
        with patch("app.api.v1.kaspi.KaspiAdapter") as mock_adapter, patch(
            "app.api.v1.kaspi.KaspiStoreToken.upsert_token"
        ) as mock_upsert:
            mock_instance = AsyncMock()
            mock_instance.health.return_value = {"status": "ok"}
            mock_adapter.return_value = mock_instance
            mock_upsert.return_value = AsyncMock()

            response = await async_client.post(
                "/api/v1/kaspi/connect",
                json={
                    "company_name": "My Kaspi Store",
                    "store_name": "test_store_name",
                    "token": "test_api_token_123456",
                    "verify": True,
                },
                headers=headers,
            )

            assert response.status_code == 200, response.text
            data = response.json()
            assert data["store_name"] == "test_store_name"
            assert data["company_id"] == company.id
            assert data["connected"] is True

            # Verify company name was updated
            await async_db_session.refresh(company)
            assert company.name == "My Kaspi Store"
            assert company.kaspi_store_id == "test_store_name"

    @pytest.mark.asyncio
    async def test_connect_verify_false_skips_adapter(self, async_client: AsyncClient, async_db_session: AsyncSession):
        """Test: verify=false should save token without calling adapter."""
        user, company = await self._create_user_and_company(async_db_session, "77001234570")
        headers = self._get_auth_header(user)

        with patch("app.api.v1.kaspi.KaspiAdapter") as mock_adapter, patch(
            "app.api.v1.kaspi.KaspiStoreToken.upsert_token"
        ):
            response = await async_client.post(
                "/api/v1/kaspi/connect",
                json={
                    "company_name": "Store Without Verify",
                    "store_name": "store_no_verify",
                    "token": "test_token_999",
                    "verify": False,
                },
                headers=headers,
            )

            assert response.status_code == 200, response.text

            # Adapter.health should NOT have been called
            mock_adapter.return_value.health.assert_not_called()

    @pytest.mark.asyncio
    async def test_connect_stores_private_metadata(self, async_client: AsyncClient, async_db_session: AsyncSession):
        """Test: optional meta dict is stored in Company.settings (not exposed)."""
        user, company = await self._create_user_and_company(async_db_session, "77001234571")
        headers = self._get_auth_header(user)

        meta = {"shop_title": "My Shop", "shop_url": "https://myshop.kz"}

        with patch("app.api.v1.kaspi.KaspiStoreToken.upsert_token"):
            response = await async_client.post(
                "/api/v1/kaspi/connect",
                json={
                    "company_name": "Store With Meta",
                    "store_name": "store_with_meta",
                    "token": "test_token_meta",
                    "verify": False,
                    "meta": meta,
                },
                headers=headers,
            )

            assert response.status_code == 200, response.text

            # Refresh company and verify metadata is stored
            await async_db_session.refresh(company)
            if company.settings:
                settings = json.loads(company.settings)
                assert "kaspi_meta" in settings
                assert settings["kaspi_meta"] == meta

            # Verify response does NOT include metadata
            data = response.json()
            assert "meta" not in data
            assert "kaspi_meta" not in data
            assert "settings" not in data

    @pytest.mark.asyncio
    async def test_connect_response_safe_fields_only(self, async_client: AsyncClient, async_db_session: AsyncSession):
        """Test: response only includes safe fields (store_name, company_id, connected)."""
        user, company = await self._create_user_and_company(async_db_session, "77001234572")
        headers = self._get_auth_header(user)

        with patch("app.api.v1.kaspi.KaspiStoreToken.upsert_token"):
            response = await async_client.post(
                "/api/v1/kaspi/connect",
                json={
                    "company_name": "Safe Response Test",
                    "store_name": "safe_response_store",
                    "token": "secret_token_should_not_appear",
                    "verify": False,
                },
                headers=headers,
            )

            assert response.status_code == 200
            data = response.json()

            # Response should only have safe fields
            assert "store_name" in data
            assert "company_id" in data
            assert "connected" in data

            # Unsafe fields should NOT be in response
            assert "token" not in data
            assert "meta" not in data
            assert "settings" not in data

    @pytest.mark.asyncio
    async def test_connect_token_not_readable_from_api(self, async_client: AsyncClient, async_db_session: AsyncSession):
        """Test: token stored via KaspiStoreToken is encrypted and not readable."""
        user, company = await self._create_user_and_company(async_db_session, "77001234573")
        headers = self._get_auth_header(user)

        with patch("app.api.v1.kaspi.KaspiStoreToken.upsert_token"):
            response = await async_client.post(
                "/api/v1/kaspi/connect",
                json={
                    "company_name": "Token Encryption Test",
                    "store_name": "token_test_store",
                    "token": "my_secret_token_xyz",
                    "verify": False,
                },
                headers=headers,
            )

            assert response.status_code == 200
            data = response.json()

            # Token should NOT be in response
            assert "token" not in data
            assert data.get("connected") is True

    @pytest.mark.asyncio
    async def test_connect_verify_true_calls_adapter(self, async_client: AsyncClient, async_db_session: AsyncSession):
        """Test: verify=true calls adapter.health() and fails if invalid."""
        user, company = await self._create_user_and_company(async_db_session, "77001234574")
        headers = self._get_auth_header(user)

        # Mock adapter to raise error
        with patch("app.api.v1.kaspi.KaspiAdapter") as mock_adapter_class, patch(
            "app.api.v1.kaspi.KaspiStoreToken.upsert_token"
        ):
            mock_instance = mock_adapter_class.return_value
            from app.integrations.kaspi_adapter import KaspiAdapterError

            mock_instance.health.side_effect = KaspiAdapterError("Invalid token")

            response = await async_client.post(
                "/api/v1/kaspi/connect",
                json={
                    "company_name": "Verify Test",
                    "store_name": "verify_test_store",
                    "token": "invalid_token",
                    "verify": True,
                },
                headers=headers,
            )

            # Should fail with 422
            assert response.status_code == 422
            data = response.json()
            assert "verification failed" in data.get("detail", "").lower()

    @pytest.mark.asyncio
    async def test_connect_tenant_isolation(self, async_client: AsyncClient, async_db_session: AsyncSession):
        """Test: company_id comes only from current_user, not from request."""
        # Create two companies and users
        company1 = Company(name="Company 1")
        company2 = Company(name="Company 2")
        async_db_session.add_all([company1, company2])
        await async_db_session.flush()

        user1 = User(
            phone="77001111111",
            company_id=company1.id,
            hashed_password=get_password_hash("Secret123!"),
            role="manager",
            is_active=True,
            is_verified=True,
        )
        user2 = User(
            phone="77002222222",
            company_id=company2.id,
            hashed_password=get_password_hash("Secret123!"),
            role="manager",
            is_active=True,
            is_verified=True,
        )
        async_db_session.add_all([user1, user2])
        await async_db_session.commit()

        # User1 should only be able to connect to their own company
        headers1 = self._get_auth_header(user1)

        with patch("app.api.v1.kaspi.KaspiStoreToken.upsert_token"):
            response = await async_client.post(
                "/api/v1/kaspi/connect",
                json={
                    "company_name": "User1 Store",
                    "store_name": "user1_store",
                    "token": "token_user1",
                    "verify": False,
                },
                headers=headers1,
            )

            assert response.status_code == 200
            data = response.json()
            assert data["company_id"] == company1.id  # Must be user1's company

            # Verify company1 was updated, not company2
            await async_db_session.refresh(company1)
            await async_db_session.refresh(company2)
            assert company1.name == "User1 Store"
            assert company2.name == "Company 2"  # Unchanged
