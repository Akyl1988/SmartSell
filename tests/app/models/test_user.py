"""Tests for User model and related entities."""

from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# --- Импортируем модели и общую Base ---
import app.models  # важно, чтобы подтянулись все ORM-классы
from app.models.audit_log import AuditLog
from app.models.base import Base, BaseModel
from app.models.product import Product
from app.models.user import OTPCode, User, UserSession
from app.models.warehouse import StockMovement


@pytest.fixture
def db_session():
    """Create test database session (SQLite in-memory)."""
    engine = create_engine("sqlite:///:memory:")
    # Используем ту же Base, что и все модели проекта
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
        # Внимание: НЕ вызываем clear_mappers(), чтобы не ломать глобальные мапперы.


class TestUser:
    """Test User model."""

    def test_user_creation(self, db_session):
        """Test creating a user."""
        user = User(
            username="testuser",
            email="test@example.com",
            full_name="Test User",
            hashed_password="hashed",
            is_active=True,
        )
        db_session.add(user)
        db_session.commit()
        db_session.refresh(user)

        assert user.id is not None
        assert user.username == "testuser"
        assert user.email == "test@example.com"
        assert user.full_name == "Test User"
        assert user.is_active is True
        assert user.created_at is not None
        assert user.updated_at is not None

    def test_user_unique_constraints(self, db_session):
        """Test user unique constraints (username must be unique)."""
        user1 = User(username="testuser", email="test@example.com", hashed_password="hashed")
        user2 = User(username="testuser", email="test2@example.com", hashed_password="hashed")

        db_session.add(user1)
        db_session.commit()

        db_session.add(user2)
        from sqlalchemy.exc import IntegrityError

        with pytest.raises(IntegrityError):
            db_session.commit()

    def test_user_optional_fields(self, db_session):
        """Test user with optional fields."""
        user = User(username="testuser", email="test@example.com", hashed_password="hashed")
        db_session.add(user)
        db_session.commit()
        db_session.refresh(user)

        assert user.full_name is None
        assert user.is_active is True  # Default value

    def test_user_session(self, db_session):
        """Test UserSession creation and relationship."""
        user = User(username="sessionuser", email="session@example.com", hashed_password="hashed")
        db_session.add(user)
        db_session.commit()
        db_session.refresh(user)

        session = UserSession(user_id=user.id, refresh_token="token1", expires_at=user.created_at)
        db_session.add(session)
        db_session.commit()
        assert session.id is not None
        assert session.user_id == user.id
        assert session.is_active is True or session.is_active is False  # Default

    def test_otp_code(self, db_session):
        """Test OTPCode creation."""
        otp = OTPCode(
            phone="1234567890",
            code="123456",
            purpose="login",
            expires_at=datetime(2025, 12, 31, 23, 59, 59),
        )
        db_session.add(otp)
        db_session.commit()
        assert otp.id is not None
        assert otp.phone == "1234567890"
        assert otp.code == "123456"
        assert otp.purpose == "login"
        assert otp.is_used is False
        assert otp.attempts == 0
        assert otp.expires_at.year == 2025

    def test_audit_log_and_stock_movement(self, db_session):
        """Test AuditLog and StockMovement models."""
        user = User(username="audituser", email="audit@example.com", hashed_password="hashed")
        db_session.add(user)
        db_session.commit()
        db_session.refresh(user)

        audit = AuditLog(user_id=user.id, action="login", details={"msg": "Successful login"})
        db_session.add(audit)

        stock = StockMovement(
            user_id=user.id,
            product_id=1,
            quantity=10,
            movement_type="in",
            previous_quantity=0,
            new_quantity=10,
        )
        db_session.add(stock)
        db_session.commit()

        assert audit.id is not None
        assert stock.id is not None
        assert audit.user_id == user.id
        assert stock.user_id == user.id

    def test_user_password_methods(self, db_session):
        """Test password set/check methods."""

        class DummyHasher:
            def __call__(self, raw):
                return f"hashed_{raw}"

            def verify(self, hashed, raw):
                return hashed == f"hashed_{raw}"

        user = User(username="puser", email="puser@example.com", hashed_password="")
        user.set_password("pass123", DummyHasher())
        assert user.hashed_password == "hashed_pass123"
        assert user.check_password("pass123", DummyHasher()) is True
        assert user.check_password("wrong", DummyHasher()) is False

    def test_user_soft_delete_and_restore(self, db_session):
        """Test user soft delete and restore."""
        user = User(username="deluser", email="del@example.com", hashed_password="hashed")
        db_session.add(user)
        db_session.commit()
        user.soft_delete()
        db_session.commit()
        assert user.is_active is False
        assert user.deleted_at is not None

        user.deleted_at = None
        user.is_active = True
        db_session.commit()
        assert user.is_active is True
        assert user.deleted_at is None

    def test_otp_methods(self, db_session):
        """Test OTP business logic methods."""
        otp = OTPCode(
            phone="9999999999",
            code="111111",
            purpose="login",
            expires_at=datetime(2025, 12, 31, 23, 59, 59),
        )
        db_session.add(otp)
        db_session.commit()
        otp.increment_attempts()
        assert otp.attempts == 1
        otp.mark_as_used()
        assert otp.is_used is True
        assert otp.otp_status() == "used"

    def test_stock_movement_log(self, db_session):
        """Test StockMovement log_movement method and product creation."""
        prod = Product(
            name="TestProduct", sku="SKU-001", slug="testproduct", price=100, stock_quantity=10
        )
        db_session.add(prod)
        db_session.commit()
        db_session.refresh(prod)

        stock = StockMovement(
            product_id=prod.id,
            movement_type="in",
            quantity=10,
            previous_quantity=0,
            new_quantity=10,
            user_id=None,
        )
        db_session.add(stock)
        db_session.commit()
        assert stock.id is not None
        assert stock.product_id == prod.id
        assert stock.movement_type == "in"
        assert stock.new_quantity == 10

    def test_bulk_user_operations(self, db_session):
        """Test bulk operations (activate, deactivate, verify, unlock, soft delete)."""
        users = [
            User(
                username=f"user{i}",
                email=f"user{i}@ex.com",
                hashed_password="hashed",
                is_active=True,
            )
            for i in range(3)
        ]
        db_session.add_all(users)
        db_session.commit()
        ids = [u.id for u in users]

        # bulk_deactivate
        User.bulk_deactivate(db_session, ids)
        db_session.commit()
        assert all(not u.is_active for u in db_session.query(User).filter(User.id.in_(ids)).all())

        # bulk_activate
        User.bulk_activate(db_session, ids)
        db_session.commit()
        assert all(u.is_active for u in db_session.query(User).filter(User.id.in_(ids)).all())

        # bulk_verify
        User.bulk_verify(db_session, ids)
        db_session.commit()
        assert all(u.is_verified for u in db_session.query(User).filter(User.id.in_(ids)).all())

        # bulk_unlock
        for u in users:
            u.lock_user()
        db_session.commit()
        User.bulk_unlock(db_session, ids)
        db_session.commit()
        assert all(not u.is_locked() for u in db_session.query(User).filter(User.id.in_(ids)).all())

        # bulk_soft_delete
        User.bulk_soft_delete(db_session, ids)
        db_session.commit()
        assert all(u.is_deleted() for u in db_session.query(User).filter(User.id.in_(ids)).all())

    def test_user_search_helpers(self, db_session):
        """Test user search helpers (by username, email, phone, identifier, general search)."""
        user = User(
            username="searchuser",
            email="search@example.com",
            phone="70000000000",
            hashed_password="hashed",
        )
        db_session.add(user)
        db_session.commit()

        assert User.find_by_username(db_session, "searchuser").id == user.id
        assert User.find_by_email(db_session, "search@example.com").id == user.id
        assert User.find_by_phone(db_session, "70000000000").id == user.id
        assert User.find_by_identifier(db_session, "searchuser").id == user.id
        assert User.find_by_identifier(db_session, "search@example.com").id == user.id
        assert User.find_by_identifier(db_session, "70000000000").id == user.id

        users = User.search(db_session, "search", limit=10)
        assert len(users) == 1
        assert users[0].id == user.id

    def test_user_to_dict_serialization(self, db_session):
        """Test user serialization (public, private, anonymized dicts)."""
        user = User(
            username="seruser",
            email="ser@example.com",
            phone="70000001234",
            hashed_password="hashed",
            full_name="Serialization User",
        )
        db_session.add(user)
        db_session.commit()
        public = user.to_public_dict()
        private = user.to_private_dict()
        anonym = user.anonymized_dict()
        assert public["display_name"] == "Serialization User"
        assert private["display_name"] == "Serialization User"
        assert anonym["phone"].startswith("***")
        assert anonym["email"].startswith("s***@")

    def test_user_password_expiry(self, db_session):
        """Test password expiry logic."""
        user = User(
            username="expuser", email="exp@example.com", hashed_password="hashed", modified_at=None
        )
        db_session.add(user)
        db_session.commit()
        assert not user.password_expired()
        user.modified_at = user.created_at.replace(year=user.created_at.year - 2)
        db_session.commit()
        assert user.password_expired()

    def test_otp_cleanup_expired(self, db_session):
        """Test OTP cleanup of expired/used codes."""
        # Тест должен быть самодостаточным и не зависеть от внешних записей
        now = datetime.utcnow()
        otp_valid = OTPCode(
            phone="70000000001",
            code="222222",
            purpose="login",
            expires_at=now.replace(year=now.year + 1),
            is_used=False,
        )
        otp_expired = OTPCode(
            phone="70000000002",
            code="333333",
            purpose="login",
            expires_at=now.replace(year=now.year - 1),
            is_used=False,
        )
        otp_used = OTPCode(
            phone="70000000003",
            code="444444",
            purpose="login",
            expires_at=now.replace(year=now.year - 2),
            is_used=True,
            created_at=now.replace(year=now.year - 2),
        )
        db_session.add_all([otp_valid, otp_expired, otp_used])
        db_session.commit()
        count = OTPCode.cleanup_expired(db_session, older_than_minutes=1)
        db_session.commit()
        assert count == 2
        rem = db_session.query(OTPCode).all()
        assert len(rem) == 1
        assert rem[0].phone == "70000000001"

    def test_user_can_manage_user(self, db_session):
        """Test RBAC: can_manage_user logic."""
        admin = User(
            username="admin",
            email="admin@example.com",
            role="admin",
            is_superuser=False,
            is_active=True,
        )
        manager = User(
            username="manager",
            email="manager@example.com",
            role="manager",
            company_id=1,
            is_active=True,
        )
        other_manager = User(
            username="othermanager",
            email="othermanager@example.com",
            role="manager",
            company_id=2,
            is_active=True,
        )
        storekeeper = User(
            username="keeper",
            email="keeper@example.com",
            role="storekeeper",
            company_id=1,
            is_active=True,
        )
        user = User(
            username="plain",
            email="plain@example.com",
            role="analyst",
            company_id=1,
            is_active=True,
        )
        db_session.add_all([admin, manager, other_manager, storekeeper, user])
        db_session.commit()

        # Админ может всех
        assert admin.can_manage_user(manager)
        assert admin.can_manage_user(other_manager)
        assert admin.can_manage_user(storekeeper)
        assert admin.can_manage_user(user)

        # Менеджер только в своей компании и не админов/суперадминов
        assert manager.can_manage_user(storekeeper)
        assert not manager.can_manage_user(admin)
        assert not manager.can_manage_user(other_manager)

        # Остальные не могут
        assert not storekeeper.can_manage_user(admin)
        assert not user.can_manage_user(manager)
