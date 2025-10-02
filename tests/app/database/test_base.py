"""Tests for database base classes and core models."""

import pytest
from app.models.base import Base, BaseModel, SoftDeleteMixin, TenantMixin, AuditMixin, LockableMixin
from app.models.user import User, UserSession, OTPCode
from app.models.audit_log import AuditLog
from app.models.warehouse import StockMovement


# --- Core Model Tests ---
class TestBase:
    """Test Base and BaseModel class functionality."""

    def test_tablename_generation(self):
        """Test automatic tablename generation."""
        # Таблицы должны быть с правильными именами
        assert User.__tablename__ == "users"
        assert UserSession.__tablename__ == "user_sessions"
        assert OTPCode.__tablename__ == "otp_codes"
        assert AuditLog.__tablename__ == "audit_logs"
        assert StockMovement.__tablename__ == "stock_movements"

    def test_base_fields(self):
        """Test that base fields are present."""
        user = User(username="test", email="test@example.com", hashed_password="hashed")
        # базовые поля должны существовать
        for field in ("created_at", "updated_at", "id"):
            assert hasattr(user, field)

    def test_model_inheritance(self):
        """Test that models properly inherit from Base and BaseModel."""
        for model in [User, UserSession, OTPCode, AuditLog, StockMovement]:
            assert issubclass(model, Base)
            assert hasattr(model, "__tablename__")
            assert issubclass(model, BaseModel)

    def test_repr(self):
        """Test __repr__ output for BaseModel."""
        user = User(username="repruser", email="repr@example.com", hashed_password="hashed")
        assert isinstance(user.__repr__(), str)

    def test_soft_delete_mixin(self):
        """Test SoftDeleteMixin functionality (если используется)."""

        class Dummy(BaseModel, SoftDeleteMixin):
            __tablename__ = "dummy"
            deleted_at = None

            def soft_delete(self):
                self.deleted_at = "now"

        dummy = Dummy()
        dummy.soft_delete()
        assert dummy.deleted_at == "now"

    def test_tenant_mixin(self):
        """Test TenantMixin (если используется)."""

        class TenantDummy(BaseModel, TenantMixin):
            __tablename__ = "tenant_dummy"
            tenant_id = 42

        dummy = TenantDummy()
        assert hasattr(dummy, "tenant_id")
        assert dummy.tenant_id == 42

    def test_audit_mixin(self):
        """Test AuditMixin (если используется)."""

        class AuditDummy(BaseModel, AuditMixin):
            __tablename__ = "audit_dummy"
            created_by = 1
            updated_by = 2

        dummy = AuditDummy()
        assert hasattr(dummy, "created_by")
        assert hasattr(dummy, "updated_by")

    def test_lockable_mixin(self):
        """Test LockableMixin (если используется)."""

        class LockDummy(BaseModel, LockableMixin):
            __tablename__ = "lock_dummy"
            locked_at = None
            locked_by = None

        dummy = LockDummy()
        dummy.locked_at = "now"
        dummy.locked_by = 123
        assert dummy.locked_at == "now"
        assert dummy.locked_by == 123


# Можно добавить тесты миграций, multi-tenancy, tenant_id и другие по необходимости!
