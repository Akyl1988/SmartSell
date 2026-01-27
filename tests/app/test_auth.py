"""
Tests for authentication functionality (legacy /api/auth/* alias supported).
"""

from datetime import timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1 import auth as auth_mod
from app.core.security import create_access_token, get_password_hash
from app.models import Company, OtpAttempt, User, UserSession
from app.utils.otp import hash_otp_code


@pytest.mark.asyncio
class TestAuth:
    """Test authentication endpoints"""

    @pytest.mark.asyncio
    async def test_register_user(self, async_client: AsyncClient):
        """Test user registration"""

        user_data = {
            "phone": "+77001234567",
            "password": "password123",
            "first_name": "Test",
            "last_name": "User",
            "company_name": "Test Company",
            "bin_iin": "123456789012",
        }

        # thanks to legacy alias this path must exist
        response = await async_client.post("/api/auth/register", json=user_data)

        assert response.status_code == 200, response.text
        data = response.json()

        assert "access_token" in data
        assert "refresh_token" in data
        assert data.get("token_type") == "bearer"

    @pytest.mark.asyncio
    async def test_register_creates_draft_company_tenant(
        self, async_client: AsyncClient, async_db_session: AsyncSession
    ):
        """Regression test: Registration should create a draft Company tenant bound to the user."""
        user_data = {
            "phone": "+77009876543",
            "password": "securepassword123",
            "company_name": "My Test Store",
        }

        # Register user
        response = await async_client.post("/api/auth/register", json=user_data)
        assert response.status_code == 200, response.text
        data = response.json()
        assert "access_token" in data

        # Refresh session to see changes from the endpoint's transaction
        await async_db_session.rollback()

        # Verify exactly one company was created
        companies = await async_db_session.execute(select(Company).where(Company.name == "My Test Store"))
        company = companies.scalars().first()
        assert company is not None, "Company should be created during registration"
        assert company.id is not None
        assert company.is_active is True
        assert company.subscription_plan == "start"

        # Verify user is bound to that company (phone is stored as digits only)
        users = await async_db_session.execute(select(User).where(User.phone == "77009876543"))
        user = users.scalars().first()
        assert user is not None
        assert user.company_id == company.id, "User should be bound to the created company"

        # Verify company owner is set to the user
        assert company.owner_id == user.id, "Company owner should be set to the user"

    @pytest.mark.asyncio
    async def test_register_creates_company_with_default_name(
        self, async_client: AsyncClient, async_db_session: AsyncSession
    ):
        """Regression test: When no company_name is provided, use 'Draft {phone}' format."""
        user_data = {
            "phone": "+77008765432",
            "password": "securepassword456",
            # no company_name provided
        }

        # Register user
        response = await async_client.post("/api/auth/register", json=user_data)
        assert response.status_code == 200, response.text

        # Refresh session to see changes from the endpoint's transaction
        await async_db_session.rollback()

        # Verify company was created with default name (phone is stored as digits only)
        users = await async_db_session.execute(select(User).where(User.phone == "77008765432"))
        user = users.scalars().first()
        assert user is not None
        assert user.company_id is not None

        companies = await async_db_session.execute(select(Company).where(Company.id == user.company_id))
        company = companies.scalars().first()
        assert company is not None
        assert company.name == "Draft 77008765432", f"Expected 'Draft 77008765432', got '{company.name}'"
        assert company.owner_id == user.id

    @pytest.mark.asyncio
    async def test_register_duplicate_phone(self, async_client: AsyncClient, async_db_session: AsyncSession):
        """Test registration with duplicate phone"""

        # Create existing user
        company = Company(name="Existing Company")
        async_db_session.add(company)
        await async_db_session.flush()

        user = User(
            company_id=company.id,
            phone="+77001234567",
            hashed_password=get_password_hash("password123"),
            role="admin",
        )
        async_db_session.add(user)
        await async_db_session.commit()

        # Try to register with same phone
        user_data = {
            "phone": "+77001234567",
            "password": "newpassword",
            "company_name": "New Company",
        }

        response = await async_client.post("/api/auth/register", json=user_data)

        assert response.status_code == 400, response.text
        detail = response.json().get("detail", "")
        assert "already" in detail.lower() or "exists" in detail.lower()

    @pytest.mark.asyncio
    async def test_login_with_password(self, async_client: AsyncClient, async_db_session: AsyncSession):
        """Test login with password"""

        # Create user
        company = Company(name="Test Company")
        async_db_session.add(company)
        await async_db_session.flush()

        user = User(
            company_id=company.id,
            phone="+77001234567",
            hashed_password=get_password_hash("password123"),
            role="admin",
        )
        async_db_session.add(user)
        await async_db_session.commit()

        # Login
        login_data = {"identifier": "+77001234567", "password": "password123"}

        response = await async_client.post("/api/auth/login", json=login_data)

        assert response.status_code == 200, response.text
        data = response.json()

        assert "access_token" in data
        assert "refresh_token" in data

    @pytest.mark.asyncio
    async def test_login_invalid_credentials(self, async_client: AsyncClient, async_db_session: AsyncSession):
        """Test login with invalid credentials"""

        # Create user
        company = Company(name="Test Company")
        async_db_session.add(company)
        await async_db_session.flush()

        user = User(
            company_id=company.id,
            phone="+77001234567",
            hashed_password=get_password_hash("password123"),
            role="admin",
        )
        async_db_session.add(user)
        await async_db_session.commit()

        # Login with wrong password
        login_data = {"identifier": "+77001234567", "password": "wrongpassword"}

        response = await async_client.post("/api/auth/login", json=login_data)

        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_login_with_otp(self, async_client: AsyncClient, async_db_session: AsyncSession):
        """Test login with OTP"""

        # Create user
        company = Company(name="Test Company")
        async_db_session.add(company)
        await async_db_session.flush()

        user = User(company_id=company.id, phone="+77001234567", role="admin")
        async_db_session.add(user)

        # Create OTP attempt
        otp_code = "123456"
        otp_attempt = OtpAttempt.create_new(phone="+77001234567", code_hash=hash_otp_code(otp_code), purpose="login")
        async_db_session.add(otp_attempt)
        await async_db_session.commit()

        # Login with OTP
        login_data = {"identifier": "+77001234567", "otp_code": otp_code}

        response = await async_client.post("/api/auth/login", json=login_data)

        assert response.status_code == 200, response.text
        data = response.json()

        assert "access_token" in data
        assert "refresh_token" in data

    @pytest.mark.asyncio
    async def test_login_with_email(self, async_client: AsyncClient, async_db_session: AsyncSession):
        company = Company(name="Email Login Co")
        async_db_session.add(company)
        await async_db_session.flush()

        user = User(
            company_id=company.id,
            phone="+77001112222",
            email="user@example.com",
            hashed_password=get_password_hash("password123"),
            role="admin",
        )
        async_db_session.add(user)
        await async_db_session.commit()

        login_data = {"identifier": "user@example.com", "password": "password123"}
        response = await async_client.post("/api/auth/login", json=login_data)
        assert response.status_code == 200, response.text
        data = response.json()
        assert "access_token" in data
        assert "refresh_token" in data

        @pytest.mark.asyncio
        async def test_password_reset_request_reset_url_dev_only(
            self,
            async_client: AsyncClient,
            async_db_session: AsyncSession,
            monkeypatch,
        ):
            company = Company(name="Reset URL Co")
            async_db_session.add(company)
            await async_db_session.flush()

            user = User(
                company_id=company.id,
                phone="77001119999",
                email="reset-url@example.com",
                hashed_password=get_password_hash("Password123!"),
                role="admin",
                is_active=True,
                is_verified=True,
            )
            async_db_session.add(user)
            await async_db_session.commit()

            async def _noop_send_email(*args, **kwargs):
                return True

            monkeypatch.setattr(auth_mod, "send_email", _noop_send_email)

            monkeypatch.setenv("ENVIRONMENT", "development")
            resp_dev = await async_client.post(
                "/api/v1/auth/password/reset/request",
                json={"identifier": "reset-url@example.com"},
            )
            assert resp_dev.status_code == 200
            dev_data = resp_dev.json().get("data") or {}
            assert "reset_url" in dev_data

            monkeypatch.setenv("ENVIRONMENT", "production")
            resp_prod = await async_client.post(
                "/api/v1/auth/password/reset/request",
                json={"identifier": "reset-url@example.com"},
            )
            assert resp_prod.status_code == 200
            prod_data = resp_prod.json().get("data") or {}
            assert "reset_url" not in prod_data

    @pytest.mark.asyncio
    async def test_phone_change_happy_path(
        self, async_client: AsyncClient, async_db_session: AsyncSession, company_a_admin_headers
    ):
        new_phone = "77009991122"
        otp_code = "123456"
        await async_db_session.rollback()
        res_user = await async_db_session.execute(select(User).where(User.phone.in_(["70000010001", "+70000010001"])))
        existing = res_user.scalars().first()
        assert existing is not None
        user_id = existing.id
        otp_attempt = OtpAttempt.create_new(
            phone=new_phone,
            code_hash=hash_otp_code(otp_code),
            purpose="phone_change",
        )
        async_db_session.add(otp_attempt)
        await async_db_session.commit()

        resp = await async_client.post(
            "/api/v1/auth/phone/change/confirm",
            headers=company_a_admin_headers,
            json={"new_phone": new_phone, "code": otp_code},
        )
        assert resp.status_code == 200, resp.text

        await async_db_session.rollback()
        async_db_session.expire_all()
        refreshed = await async_db_session.get(User, user_id)
        assert refreshed is not None
        assert refreshed.phone == new_phone
        assert refreshed.is_verified is True

    @pytest.mark.asyncio
    async def test_phone_change_uniqueness_rejected(
        self, async_client: AsyncClient, async_db_session: AsyncSession, company_a_admin_headers
    ):
        company = Company(name="Phone Change Co")
        async_db_session.add(company)
        await async_db_session.flush()

        taken = User(
            company_id=company.id,
            phone="77008887766",
            hashed_password=get_password_hash("Secret123!"),
            role="admin",
            is_active=True,
            is_verified=True,
        )
        async_db_session.add(taken)
        await async_db_session.commit()

        resp = await async_client.post(
            "/api/v1/auth/phone/change/request",
            headers=company_a_admin_headers,
            json={"new_phone": "77008887766"},
        )
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_phone_change_request_no_dev_code_in_production(
        self, async_client: AsyncClient, company_a_admin_headers, monkeypatch
    ):
        monkeypatch.setenv("ENVIRONMENT", "production")
        resp = await async_client.post(
            "/api/v1/auth/phone/change/request",
            headers=company_a_admin_headers,
            json={"new_phone": "77007776655"},
        )
        assert resp.status_code == 200, resp.text
        data = resp.json().get("data") or {}
        assert "dev_code" not in data

    @pytest.mark.asyncio
    async def test_send_otp(self, async_client: AsyncClient):
        """Test sending OTP"""

        # В реальном тесте сервис Mobizon нужно мокать.
        # Здесь проверяем только, что эндпоинт существует и отвечает корректным кодом.
        response = await async_client.post(
            "/api/auth/send-otp",
            params={"phone": "+77001234567", "purpose": "login"},
        )

        # В зависимости от конфигурации может вернуться 200 (успех) или 500 (ошибка внешнего сервиса).
        assert response.status_code in (200, 500)

    @pytest.mark.asyncio
    async def test_send_otp_hides_provider_in_production(self, async_client: AsyncClient, monkeypatch):
        monkeypatch.setenv("ENVIRONMENT", "production")
        monkeypatch.delenv("DEBUG_PROVIDER_INFO", raising=False)

        response = await async_client.post(
            "/api/auth/send-otp",
            params={"phone": "+77001234567", "purpose": "login"},
        )

        assert response.status_code in (200, 500)
        data = response.json().get("data") or {}
        assert "provider" not in data
        assert "provider_version" not in data

    @pytest.mark.asyncio
    async def test_send_otp_shows_provider_when_allowed(self, async_client: AsyncClient, monkeypatch):
        monkeypatch.setenv("ENVIRONMENT", "development")
        monkeypatch.setenv("DEBUG_PROVIDER_INFO", "1")

        response = await async_client.post(
            "/api/auth/send-otp",
            params={"phone": "+77001234567", "purpose": "login"},
        )

        assert response.status_code in (200, 500)
        data = response.json().get("data") or {}
        assert "provider" in data
        assert "provider_version" in data

    @pytest.mark.asyncio
    async def test_refresh_token(self, async_client: AsyncClient, async_db_session: AsyncSession):
        """Test token refresh"""

        # Create user and get tokens first
        company = Company(name="Test Company")
        async_db_session.add(company)
        await async_db_session.flush()

        user = User(
            company_id=company.id,
            phone="+77001234567",
            hashed_password=get_password_hash("password123"),
            role="admin",
        )
        async_db_session.add(user)
        await async_db_session.commit()

        # Login to get tokens
        login_data = {"identifier": "+77001234567", "password": "password123"}

        login_response = await async_client.post("/api/auth/login", json=login_data)
        assert login_response.status_code == 200, login_response.text
        tokens = login_response.json()

        # Use refresh token
        refresh_data = {"refresh_token": tokens["refresh_token"]}
        response = await async_client.post("/api/auth/token/refresh", json=refresh_data)

        assert response.status_code == 200, response.text
        data = response.json()

        assert "access_token" in data
        assert "refresh_token" in data

    @pytest.mark.asyncio
    async def test_refresh_rotation_and_reuse_detection(
        self, async_client: AsyncClient, async_db_session: AsyncSession
    ):
        company = Company(name="Test Company")
        async_db_session.add(company)
        await async_db_session.flush()

        user = User(
            company_id=company.id,
            phone="+77001234567",
            hashed_password=get_password_hash("password123"),
            role="admin",
        )
        async_db_session.add(user)
        await async_db_session.commit()

        login_data = {"identifier": "+77001234567", "password": "password123"}
        login_response = await async_client.post("/api/auth/login", json=login_data)
        assert login_response.status_code == 200, login_response.text
        tokens = login_response.json()
        old_refresh = tokens["refresh_token"]

        refresh_data = {"refresh_token": old_refresh}
        refresh_response = await async_client.post("/api/auth/token/refresh", json=refresh_data)
        assert refresh_response.status_code == 200
        rotated = refresh_response.json()
        assert rotated["refresh_token"] != old_refresh

        reuse_response = await async_client.post("/api/auth/token/refresh", json=refresh_data)
        assert reuse_response.status_code == 401
        assert reuse_response.json().get("detail") == "session_terminated"

        await async_db_session.rollback()
        sessions = (
            (
                await async_db_session.execute(
                    select(UserSession).where(UserSession.user_id == user.id, UserSession.is_active.is_(True))
                )
            )
            .scalars()
            .all()
        )
        assert sessions == []

    @pytest.mark.asyncio
    async def test_refresh_expired(self, async_client: AsyncClient, async_db_session: AsyncSession):
        company = Company(name="Test Company")
        async_db_session.add(company)
        await async_db_session.flush()

        user = User(
            company_id=company.id,
            phone="+77001234567",
            hashed_password=get_password_hash("password123"),
            role="admin",
        )
        async_db_session.add(user)
        await async_db_session.commit()

        login_data = {"identifier": "+77001234567", "password": "password123"}
        login_response = await async_client.post("/api/auth/login", json=login_data)
        assert login_response.status_code == 200, login_response.text
        tokens = login_response.json()

        await async_db_session.rollback()
        session = (
            (await async_db_session.execute(select(UserSession).where(UserSession.user_id == user.id)))
            .scalars()
            .first()
        )
        assert session is not None
        session.expires_at = auth_mod._utcnow_naive() - timedelta(seconds=1)
        await async_db_session.commit()

        refresh_data = {"refresh_token": tokens["refresh_token"]}
        response = await async_client.post("/api/auth/token/refresh", json=refresh_data)
        assert response.status_code == 401
        assert response.json().get("detail") == "refresh_invalid"

    @pytest.mark.asyncio
    async def test_logout_revokes_session(self, async_client: AsyncClient, async_db_session: AsyncSession):
        company = Company(name="Test Company")
        async_db_session.add(company)
        await async_db_session.flush()

        user = User(
            company_id=company.id,
            phone="+77001234567",
            hashed_password=get_password_hash("password123"),
            role="admin",
        )
        async_db_session.add(user)
        await async_db_session.commit()

        login_data = {"identifier": "+77001234567", "password": "password123"}
        login_response = await async_client.post("/api/auth/login", json=login_data)
        assert login_response.status_code == 200, login_response.text
        tokens = login_response.json()

        logout_resp = await async_client.post("/api/auth/logout", json={"refresh_token": tokens["refresh_token"]})
        assert logout_resp.status_code == 200

        refresh_resp = await async_client.post(
            "/api/auth/token/refresh",
            json={"refresh_token": tokens["refresh_token"]},
        )
        assert refresh_resp.status_code == 401
        assert refresh_resp.json().get("detail") in {"refresh_invalid", "session_terminated"}

    @pytest.mark.asyncio
    async def test_get_current_user(self, async_client: AsyncClient, async_db_session: AsyncSession):
        """Test getting current user info"""

        # Create user and get token
        company = Company(name="Test Company")
        async_db_session.add(company)
        await async_db_session.flush()

        user = User(
            company_id=company.id,
            phone="+77001234567",
            hashed_password=get_password_hash("password123"),
            role="admin",
            first_name="Test",
            last_name="User",
        )
        async_db_session.add(user)
        await async_db_session.commit()

        # Login to get token
        login_data = {"identifier": "+77001234567", "password": "password123"}

        login_response = await async_client.post("/api/auth/login", json=login_data)
        assert login_response.status_code == 200, login_response.text
        tokens = login_response.json()

        # Get user info
        headers = {"Authorization": f"Bearer {tokens['access_token']}"}
        response = await async_client.get("/api/auth/me", headers=headers)

        assert response.status_code == 200, response.text
        data = response.json()

        assert data["phone"] == "+77001234567"
        assert data.get("first_name") == "Test"
        assert data.get("last_name") == "User"
        assert data.get("role") == "admin"
        assert data.get("company_id") == company.id
        assert data.get("company_name") == "Test Company"

    @pytest.mark.asyncio
    async def test_access_token_expired_returns_token_expired(
        self, async_client: AsyncClient, async_db_session: AsyncSession
    ):
        company = Company(name="Test Company")
        async_db_session.add(company)
        await async_db_session.flush()

        user = User(
            company_id=company.id,
            phone="+77001234567",
            hashed_password=get_password_hash("password123"),
            role="admin",
        )
        async_db_session.add(user)
        await async_db_session.commit()

        expired_token = create_access_token(subject=user.id, expires_delta=timedelta(seconds=-1))
        headers = {"Authorization": f"Bearer {expired_token}"}
        response = await async_client.get("/api/auth/me", headers=headers)

        assert response.status_code == 401
        assert response.json().get("detail") == "token_expired"

    @pytest.mark.asyncio
    async def test_change_password(self, async_client: AsyncClient, async_db_session: AsyncSession):
        """Test password change"""

        # Create user
        company = Company(name="Test Company")
        async_db_session.add(company)
        await async_db_session.flush()

        user = User(
            company_id=company.id,
            phone="+77001234567",
            hashed_password=get_password_hash("oldpassword"),
            role="admin",
        )
        async_db_session.add(user)
        await async_db_session.commit()

        # Login to get token
        login_data = {"identifier": "+77001234567", "password": "oldpassword"}

        login_response = await async_client.post("/api/auth/login", json=login_data)
        assert login_response.status_code == 200, login_response.text
        tokens = login_response.json()

        # Change password
        headers = {"Authorization": f"Bearer {tokens['access_token']}"}
        password_data = {
            "current_password": "oldpassword",
            "new_password": "newpassword123",
        }

        response = await async_client.post("/api/auth/change-password", json=password_data, headers=headers)

        assert response.status_code == 200, response.text

        # Verify new password works
        login_data = {"identifier": "+77001234567", "password": "newpassword123"}

        response = await async_client.post("/api/auth/login", json=login_data)

        assert response.status_code == 200, response.text

    @pytest.mark.asyncio
    async def test_unauthorized_access(self, async_client: AsyncClient):
        """Test accessing protected endpoint without token"""

        response = await async_client.get("/api/auth/me")

        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_invalid_token(self, async_client: AsyncClient):
        """Test accessing protected endpoint with invalid token"""

        headers = {"Authorization": "Bearer invalid_token"}

        response = await async_client.get("/api/auth/me", headers=headers)

        assert response.status_code == 401


# --------------------------------------------------------------------------------------
# ВАЖНО: никаких локальных фикстур db_session тут НЕ объявляем,
# чтобы не переопределить test DB из tests/conftest.py.
# --------------------------------------------------------------------------------------


# Доп. утилиты (чистые фабрики моделей без побочных эффектов)
def create_test_company(name: str = "Test Company") -> Company:
    """Create test company (detached, must be added to session by caller)."""
    return Company(name=name)


def create_test_user(
    company_id: int,
    phone: str = "+77001234567",
    password: str = "password123",
    role: str = "admin",
) -> User:
    """Create test user (detached, must be added to session by caller)."""
    return User(
        company_id=company_id,
        phone=phone,
        hashed_password=get_password_hash(password),
        role=role,
    )
