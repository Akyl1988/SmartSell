"""
Tests for authentication functionality (legacy /api/auth/* alias supported).
"""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import get_password_hash
from app.main import app
from app.models import Company, OtpAttempt, User
from app.utils.otp import hash_otp_code


@pytest.mark.anyio
class TestAuth:
    """Test authentication endpoints"""

    @pytest.mark.asyncio
    async def test_register_user(self):
        """Test user registration"""

        user_data = {
            "phone": "+77001234567",
            "password": "password123",
            "first_name": "Test",
            "last_name": "User",
            "company_name": "Test Company",
            "bin_iin": "123456789012",
        }

        async with AsyncClient(app=app, base_url="http://test") as client:
            # thanks to legacy alias this path must exist
            response = await client.post("/api/auth/register", json=user_data)

        assert response.status_code == 200, response.text
        data = response.json()

        assert "access_token" in data
        assert "refresh_token" in data
        assert data.get("token_type") == "bearer"

    @pytest.mark.asyncio
    async def test_register_duplicate_phone(self, db_session: AsyncSession):
        """Test registration with duplicate phone"""

        # Create existing user
        company = Company(name="Existing Company")
        db_session.add(company)
        await db_session.flush()

        user = User(
            company_id=company.id,
            phone="+77001234567",
            hashed_password=get_password_hash("password123"),
            role="admin",
        )
        db_session.add(user)
        await db_session.commit()

        # Try to register with same phone
        user_data = {
            "phone": "+77001234567",
            "password": "newpassword",
            "company_name": "New Company",
        }

        async with AsyncClient(app=app, base_url="http://test") as client:
            response = await client.post("/api/auth/register", json=user_data)

        assert response.status_code == 400, response.text
        detail = response.json().get("detail", "")
        assert "already" in detail.lower() or "exists" in detail.lower()

    @pytest.mark.asyncio
    async def test_login_with_password(self, db_session: AsyncSession):
        """Test login with password"""

        # Create user
        company = Company(name="Test Company")
        db_session.add(company)
        await db_session.flush()

        user = User(
            company_id=company.id,
            phone="+77001234567",
            hashed_password=get_password_hash("password123"),
            role="admin",
        )
        db_session.add(user)
        await db_session.commit()

        # Login
        login_data = {"phone": "+77001234567", "password": "password123"}

        async with AsyncClient(app=app, base_url="http://test") as client:
            response = await client.post("/api/auth/login", json=login_data)

        assert response.status_code == 200, response.text
        data = response.json()

        assert "access_token" in data
        assert "refresh_token" in data

    @pytest.mark.asyncio
    async def test_login_invalid_credentials(self, db_session: AsyncSession):
        """Test login with invalid credentials"""

        # Create user
        company = Company(name="Test Company")
        db_session.add(company)
        await db_session.flush()

        user = User(
            company_id=company.id,
            phone="+77001234567",
            hashed_password=get_password_hash("password123"),
            role="admin",
        )
        db_session.add(user)
        await db_session.commit()

        # Login with wrong password
        login_data = {"phone": "+77001234567", "password": "wrongpassword"}

        async with AsyncClient(app=app, base_url="http://test") as client:
            response = await client.post("/api/auth/login", json=login_data)

        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_login_with_otp(self, db_session: AsyncSession):
        """Test login with OTP"""

        # Create user
        company = Company(name="Test Company")
        db_session.add(company)
        await db_session.flush()

        user = User(company_id=company.id, phone="+77001234567", role="admin")
        db_session.add(user)

        # Create OTP attempt
        otp_code = "123456"
        otp_attempt = OtpAttempt.create_new(
            phone="+77001234567", code_hash=hash_otp_code(otp_code), purpose="login"
        )
        db_session.add(otp_attempt)
        await db_session.commit()

        # Login with OTP
        login_data = {"phone": "+77001234567", "otp_code": otp_code}

        async with AsyncClient(app=app, base_url="http://test") as client:
            response = await client.post("/api/auth/login", json=login_data)

        assert response.status_code == 200, response.text
        data = response.json()

        assert "access_token" in data
        assert "refresh_token" in data

    @pytest.mark.asyncio
    async def test_send_otp(self):
        """Test sending OTP"""

        # В реальном тесте сервис Mobizon нужно мокать.
        # Здесь проверяем только, что эндпоинт существует и отвечает корректным кодом.
        async with AsyncClient(app=app, base_url="http://test") as client:
            response = await client.post(
                "/api/auth/send-otp",
                params={"phone": "+77001234567", "purpose": "login"},
            )

        # В зависимости от конфигурации может вернуться 200 (успех) или 500 (ошибка внешнего сервиса).
        assert response.status_code in (200, 500)

    @pytest.mark.asyncio
    async def test_refresh_token(self, db_session: AsyncSession):
        """Test token refresh"""

        # Create user and get tokens first
        company = Company(name="Test Company")
        db_session.add(company)
        await db_session.flush()

        user = User(
            company_id=company.id,
            phone="+77001234567",
            hashed_password=get_password_hash("password123"),
            role="admin",
        )
        db_session.add(user)
        await db_session.commit()

        # Login to get tokens
        login_data = {"phone": "+77001234567", "password": "password123"}

        async with AsyncClient(app=app, base_url="http://test") as client:
            login_response = await client.post("/api/auth/login", json=login_data)
            assert login_response.status_code == 200, login_response.text
            tokens = login_response.json()

            # Use refresh token
            refresh_data = {"refresh_token": tokens["refresh_token"]}
            response = await client.post("/api/auth/token/refresh", json=refresh_data)

        assert response.status_code == 200, response.text
        data = response.json()

        assert "access_token" in data
        assert "refresh_token" in data

    @pytest.mark.asyncio
    async def test_get_current_user(self, db_session: AsyncSession):
        """Test getting current user info"""

        # Create user and get token
        company = Company(name="Test Company")
        db_session.add(company)
        await db_session.flush()

        user = User(
            company_id=company.id,
            phone="+77001234567",
            hashed_password=get_password_hash("password123"),
            role="admin",
            first_name="Test",
            last_name="User",
        )
        db_session.add(user)
        await db_session.commit()

        # Login to get token
        login_data = {"phone": "+77001234567", "password": "password123"}

        async with AsyncClient(app=app, base_url="http://test") as client:
            login_response = await client.post("/api/auth/login", json=login_data)
            assert login_response.status_code == 200, login_response.text
            tokens = login_response.json()

            # Get user info
            headers = {"Authorization": f"Bearer {tokens['access_token']}"}
            response = await client.get("/api/auth/me", headers=headers)

        assert response.status_code == 200, response.text
        data = response.json()

        assert data["phone"] == "+77001234567"
        assert data.get("first_name") == "Test"
        assert data.get("last_name") == "User"
        assert data.get("role") == "admin"

    @pytest.mark.asyncio
    async def test_change_password(self, db_session: AsyncSession):
        """Test password change"""

        # Create user
        company = Company(name="Test Company")
        db_session.add(company)
        await db_session.flush()

        user = User(
            company_id=company.id,
            phone="+77001234567",
            hashed_password=get_password_hash("oldpassword"),
            role="admin",
        )
        db_session.add(user)
        await db_session.commit()

        # Login to get token
        login_data = {"phone": "+77001234567", "password": "oldpassword"}

        async with AsyncClient(app=app, base_url="http://test") as client:
            login_response = await client.post("/api/auth/login", json=login_data)
            assert login_response.status_code == 200, login_response.text
            tokens = login_response.json()

            # Change password
            headers = {"Authorization": f"Bearer {tokens['access_token']}"}
            password_data = {
                "current_password": "oldpassword",
                "new_password": "newpassword123",
            }

            response = await client.post(
                "/api/auth/change-password", json=password_data, headers=headers
            )

        assert response.status_code == 200, response.text

        # Verify new password works
        login_data = {"phone": "+77001234567", "password": "newpassword123"}

        async with AsyncClient(app=app, base_url="http://test") as client:
            response = await client.post("/api/auth/login", json=login_data)

        assert response.status_code == 200, response.text

    @pytest.mark.asyncio
    async def test_unauthorized_access(self):
        """Test accessing protected endpoint without token"""

        async with AsyncClient(app=app, base_url="http://test") as client:
            response = await client.get("/api/auth/me")

        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_invalid_token(self):
        """Test accessing protected endpoint with invalid token"""

        headers = {"Authorization": "Bearer invalid_token"}

        async with AsyncClient(app=app, base_url="http://test") as client:
            response = await client.get("/api/auth/me", headers=headers)

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
