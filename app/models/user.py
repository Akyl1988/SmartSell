# app/models/user.py
"""
User, UserSession, OTPCode models for authentication, user management, and business logic.

Production-minded details:
- Multi-tenant, audit, soft-delete, lockable mixins
- UTC naive datetime everywhere
- Strict role/OTP validation, safe defaults
- Unique/Check constraints + helpful indexes
- Useful helpers, safe serialization, business logic
- PostgreSQL-friendly server defaults

Дополнения:
- RBAC-помощники (can_manage_user и др.)
- Поисковые/утилитарные classmethod'ы (find_by_*, search, get_or_create)
- search_text/display_label/is_archived свойства
- Массовые операции (bulk_soft_delete, bulk_unlock, bulk_verify, bulk_deactivate, bulk_activate)
- Защита от конфликтов логинов (ensure_unique_identifiers)
- Методы ротации пароля и сброса блокировки
- Очистка устаревших OTP и ограничение попыток
- Безопасная сериализация to_public_dict / to_private_dict
- Валидация дат из строк (_parse_dt) сохранена

Новые улучшения (без ломки контракта):
- get_stock_movements: сортировка без жёсткого импорта моделей (лениво через mapper)
- ensure_has_any_identifier: проверка наличия хотя бы одного идентификатора
- to_public_dict / to_private_dict: can_login, last_login_at_iso
- OTP.cleanup_expired: удаляет просроченные И давние использованные коды
- bulk_deactivate: не отключает суперадминов
- ВАЖНО: связь с Company настроена с явным foreign_keys, чтобы избежать AmbiguousForeignKeysError
- Highload: блокировки (SELECT FOR UPDATE / advisory), async CRUD/поиск/батчи
- Test factories: удобные .factory() для быстрого поднятия фикстур
- Event hooks: мягкая нормализация username/phone/email и auto-verify для корпоративных доменов (опционально)

Доп. правки (надежность прод):
- UserSession.expires_at получает безопасный дефолт (+30 дней) и форсируется слушателями даже если в коде пришёл None.
- Добавлен seconds_left и защитные утилиты по сессиям.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional, Tuple, Union, TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
    text,
    or_,
    and_,
    event,
    select,
)
from sqlalchemy.orm import declarative_mixin, relationship, validates, Session


# -----------------------------------------------------------------------------
# Small time helper (UTC-naive, как в проекте)
# -----------------------------------------------------------------------------
def utc_now() -> datetime:
    return datetime.utcnow()


# -----------------------------------------------------------------------------
# LenientInitMixin — мягкий конструктор, чтобы не падать на kwargs до инициализации маппера
# Пытаемся импортировать из base.py; если нет — даём локальный фоллбэк.
# -----------------------------------------------------------------------------
try:
    from app.models.base import LenientInitMixin  # type: ignore
except Exception:

    class LenientInitMixin:  # type: ignore
        """Мягкий конструктор: принимает любые kwargs и выставляет их как атрибуты.
        Не конфликтует с SQLAlchemy: instrumented-поля будут работать как обычно.
        """

        def __init__(self, **kwargs):
            try:
                super().__init__()  # у declarative обычно пусто
            except Exception:
                pass
            for k, v in (kwargs or {}).items():
                try:
                    setattr(self, k, v)
                except Exception:
                    pass


# Лёгкие импорты: не тянем тяжёлые модули на уровень файла
if not TYPE_CHECKING:
    try:
        from app.models.company import Company  # noqa: F401
    except Exception:
        pass
    try:
        # в реальном проекте модуль может называться audit или audit_log — оставляем try/except
        from app.models.audit import AuditLog  # noqa: F401
    except Exception:
        try:
            from app.models.audit_log import AuditLog  # noqa: F401
        except Exception:
            pass
    try:
        # StockMovement обычно живёт в app.models.warehouse
        from app.models.warehouse import StockMovement  # noqa: F401
    except Exception:
        pass

from app.models.base import (
    BaseModel,
    SoftDeleteMixin,
    TenantMixin,
    AuditMixin,
    LockableMixin,
    # highload-lock helpers
    for_update_by_id,
    pg_advisory_xact_lock,
    # async helpers (опциональны, но аннотации оставляем)
    aexists,
    afirst,
    aget_by_id,
    acreate,
    aupdate,
    adelete,
    abulk_update_rows,
    afor_update_by_id,
    apg_advisory_xact_lock,
)

# ======================================================================================
# Constants & Policies
# ======================================================================================
ALLOWED_ROLES = {"admin", "manager", "storekeeper", "analyst"}
EMAIL_REGEX = re.compile(r"^[^@]+@[^@]+\.[^@]+$")
PHONE_REGEX = re.compile(r"^\+?\d{10,15}$")

MAX_FAILED_LOGIN_ATTEMPTS = 5
LOCK_MINUTES = 15
PASSWORD_MAX_AGE_DAYS = 365

OTP_MAX_ATTEMPTS = 5
OTP_LENGTH = 6
OTP_EXPIRY_MINUTES = 10

# Корпоративные домены, которым можно доверять для auto-verify (по желанию)
TRUSTED_EMAIL_DOMAINS: tuple[str, ...] = ("@corp.local", "@company.com")


def _parse_dt(value: Union[str, datetime, None]) -> Optional[datetime]:
    """Accept datetime or ISO string; return UTC-naive datetime or None."""
    if value is None or isinstance(value, datetime):
        dt = value
    else:
        v = value.strip()
        try:
            if v.endswith("Z"):
                v = v[:-1]
            v = v.replace("T", " ")
            dt = datetime.fromisoformat(v)
        except Exception:
            try:
                dt = datetime.strptime(v, "%Y-%m-%d %H:%M:%S")
            except Exception:
                raise ValueError(f"Invalid datetime format: {value!r}")
    if dt is not None and getattr(dt, "tzinfo", None) is not None:
        dt = dt.replace(tzinfo=None)
    return dt


@declarative_mixin
class VersionedMixin:
    """Optimistic locking via version column."""

    version = Column(Integer, nullable=False, default=0, server_default=text("0"))
    __mapper_args__ = {"version_id_col": version}


# ======================================================================================
# User
# ======================================================================================
class User(
    LenientInitMixin,
    BaseModel,
    TenantMixin,
    SoftDeleteMixin,
    AuditMixin,
    LockableMixin,
    VersionedMixin,
):
    __tablename__ = "users"
    __allow_unmapped__ = True

    id = Column(Integer, primary_key=True, index=True)

    # Login identifiers (at least one should be provided at business layer)
    username = Column(String(50), unique=True, index=True, nullable=True)
    phone = Column(String(20), unique=True, index=True, nullable=True)
    email = Column(String(255), unique=True, index=True, nullable=True)

    # Password (allow empty by tests – safe default)
    hashed_password = Column(
        String(255),
        nullable=False,
        default="",
        server_default=text("''"),
    )

    # Status / role
    role = Column(String(32), nullable=False, default="manager", server_default=text("'manager'"))
    is_active = Column(Boolean, nullable=False, default=True, server_default=text("true"))
    is_verified = Column(Boolean, nullable=False, default=False, server_default=text("false"))
    is_superuser = Column(Boolean, nullable=False, default=False, server_default=text("false"))

    # Service fields
    full_name = Column(String(255), nullable=True)
    last_login_at = Column(DateTime, nullable=True)
    failed_login_attempts = Column(Integer, nullable=False, default=0, server_default=text("0"))
    locked_until = Column(DateTime, nullable=True)

    # Org / audit / locks / deletion
    company_id = Column(ForeignKey("companies.id", ondelete="SET NULL"), nullable=True, index=True)
    last_modified_by = Column(Integer, nullable=True, index=True)
    modified_at = Column(DateTime, nullable=True)
    locked_at = Column(DateTime, nullable=True)
    locked_by = Column(Integer, nullable=True, index=True)
    deleted_at = Column(DateTime, nullable=True, index=True)

    # Timestamps
    created_at = Column(
        DateTime, nullable=False, default=utc_now, server_default=func.now()
    )
    updated_at = Column(DateTime, nullable=False, default=utc_now, onupdate=utc_now)

    # Relationships
    # Явно указываем foreign_keys, чтобы не конфликтовать с Company.owner_id
    company = relationship(
        "Company",
        back_populates="users",
        foreign_keys=[company_id],
        lazy="joined",
    )
    sessions = relationship(
        "UserSession", back_populates="user", cascade="all, delete-orphan", lazy="selectin"
    )

    # Симметрия с AuditLog.user (см. app/models/audit.py)
    audit_logs = relationship(
        "AuditLog",
        back_populates="user",
        cascade="all, delete-orphan",
        foreign_keys="AuditLog.user_id",
        lazy="dynamic",
    )
    # Аналогично для складских движений (ожидается back_populates="user" на стороне StockMovement)
    stock_movements = relationship(
        "StockMovement",
        back_populates="user",
        cascade="all, delete-orphan",
        foreign_keys="StockMovement.user_id",
        lazy="dynamic",
    )
    otp_codes = relationship(
        "OTPCode", back_populates="user", cascade="all, delete-orphan", lazy="dynamic"
    )

    __table_args__ = (
        Index("ix_users_active_role", "is_active", "role"),
        Index("ix_users_company_active", "company_id", "is_active"),
        Index("ix_users_login_fields", "username", "phone", "email"),
        # Частый фильтр: быстро найти по одной из идентификаций
        Index("ix_users_username_email_lower", func.lower(username), func.lower(email)),
        CheckConstraint("failed_login_attempts >= 0", name="ck_user_failed_login_nonneg"),
        CheckConstraint(
            "role IN ('admin','manager','storekeeper','analyst')", name="ck_user_role_allowed"
        ),
    )

    # ---------------- Normalization / Validation ----------------
    @validates("email")
    def _validate_email(self, _key, value: Optional[str]):
        if value is None:
            return value
        v = value.lower().strip()
        if v and not EMAIL_REGEX.match(v):
            raise ValueError("Invalid email format")
        return v

    @validates("username")
    def _strip_username(self, _key, value: Optional[str]):
        return value.strip() if isinstance(value, str) else value

    @validates("phone")
    def _validate_phone(self, _key, value: Optional[str]):
        if value is None:
            return value
        v = value.strip()
        if v and not PHONE_REGEX.match(v):
            raise ValueError("Invalid phone format")
        return v

    @validates("role")
    def _validate_role(self, _key, value: str):
        v = (value or "").strip()
        if v not in ALLOWED_ROLES:
            raise ValueError(f"Invalid role: {v}")
        return v

    @validates(
        "last_login_at",
        "locked_until",
        "modified_at",
        "locked_at",
        "deleted_at",
        "created_at",
        "updated_at",
    )
    def _coerce_dt(self, _key, value):
        return _parse_dt(value)

    # ---------------- Derived properties ----------------
    @property
    def is_archived(self) -> bool:
        return self.deleted_at is not None

    @property
    def search_text(self) -> str:
        parts = [self.display_name(), self.username or "", self.email or "", self.phone or ""]
        return " ".join(p for p in parts if p).strip().lower()

    def display_name(self) -> str:
        for val in [self.full_name, self.username, self.phone, self.email]:
            if val:
                return val
        return f"User#{self.id}"

    # ---------------- Representation ----------------
    def __repr__(self) -> str:
        return f"<User(id={self.id}, username={self.username!r}, phone={self.phone!r}, role={self.role!r})>"

    # ---------------- Password ----------------
    def set_password(self, raw_password: str, hasher) -> None:
        if not raw_password:
            raise ValueError("Password cannot be empty")
        self.hashed_password = hasher(raw_password)
        self.modified_at = utc_now()

    def check_password(self, raw_password: str, hasher) -> bool:
        try:
            return hasher.verify(self.hashed_password, raw_password)  # type: ignore[attr-defined]
        except AttributeError:
            try:
                return self.hashed_password == hasher(raw_password)
            except Exception:
                return False
        except Exception:
            return False

    def password_expired(self) -> bool:
        if not self.modified_at:
            return False
        return (utc_now() - self.modified_at).days > PASSWORD_MAX_AGE_DAYS

    def rotate_password(self, raw_password: str, hasher) -> None:
        """Сменить пароль и сбросить блокировки/счётчики."""
        self.set_password(raw_password, hasher)
        self.reset_failed_logins()
        self.is_active = True

    def force_password_reset(self) -> None:
        self.failed_login_attempts = MAX_FAILED_LOGIN_ATTEMPTS
        self.locked_until = utc_now() + timedelta(minutes=LOCK_MINUTES)
        self.is_active = False

    # ---------------- Logins ----------------
    def mark_login(self) -> None:
        self.last_login_at = utc_now()
        self.failed_login_attempts = 0

    def increment_failed_login(self) -> None:
        self.failed_login_attempts += 1
        if self.failed_login_attempts >= MAX_FAILED_LOGIN_ATTEMPTS:
            self.locked_until = utc_now() + timedelta(minutes=LOCK_MINUTES)
            self.is_active = False

    def reset_failed_logins(self) -> None:
        self.failed_login_attempts = 0
        self.locked_until = None
        if not self.is_archived:
            self.is_active = True

    # ---------------- Status / Flags ----------------
    def verify(self) -> None:
        self.is_verified = True

    def soft_delete(self) -> None:
        self.deleted_at = utc_now()
        self.is_active = False

    def lock_user(self, user_id: Optional[int] = None) -> None:
        self.locked_at = utc_now()
        self.locked_by = user_id

    def unlock_user(self) -> None:
        self.locked_at = None
        self.locked_by = None
        self.locked_until = None
        if self.deleted_at is None:
            self.is_active = True

    def set_modified_by(self, user_id: int) -> None:
        self.last_modified_by = user_id
        self.modified_at = utc_now()

    def activate(self) -> None:
        if not self.is_locked() and not self.is_deleted():
            self.is_active = True

    def deactivate(self) -> None:
        self.is_active = False

    # ---------------- Checks ----------------
    def is_locked(self) -> bool:
        now = utc_now()
        return self.locked_at is not None or (
            self.locked_until is not None and self.locked_until > now
        )

    def is_deleted(self) -> bool:
        return self.deleted_at is not None

    def can_login(self) -> bool:
        return self.is_active and not self.is_locked() and not self.is_deleted()

    def is_verified_user(self) -> bool:
        return self.is_active and self.is_verified and not self.is_deleted()

    def can_be_deleted(self) -> bool:
        return not self.is_superuser and not self.is_locked()

    def can_be_locked(self) -> bool:
        return not self.is_locked() and not self.is_deleted()

    # ---------------- Role Checks / RBAC ----------------
    def is_admin(self) -> bool:
        return self.role == "admin" or self.is_superuser

    def is_manager(self) -> bool:
        return self.role == "manager"

    def is_storekeeper(self) -> bool:
        return self.role == "storekeeper"

    def is_analyst(self) -> bool:
        return self.role == "analyst"

    def can_manage_user(self, other: "User") -> bool:
        """
        Админ может всех; менеджер — только в своей компании и не админов; остальные — никто.
        """
        if self.is_superuser or self.is_admin():
            return True
        if (
            self.is_manager()
            and (self.company_id is not None)
            and (self.company_id == other.company_id)
        ):
            return not (other.is_admin() or other.is_superuser)
        return False

    # ---------------- Utilities / Serialization ----------------
    def to_dict(self, exclude_sensitive: bool = False) -> Dict[str, Any]:
        data = {col.name: getattr(self, col.name) for col in self.__table__.columns}
        if exclude_sensitive:
            data.pop("hashed_password", None)
        return data

    def to_public_dict(self) -> Dict[str, Any]:
        d = self.to_dict(exclude_sensitive=True)
        d["display_name"] = self.display_name()
        d["is_archived"] = self.is_archived
        d["can_login"] = self.can_login()
        d["last_login_at_iso"] = (
            self.last_login_at.isoformat(timespec="seconds") if self.last_login_at else None
        )
        return d

    def to_private_dict(self) -> Dict[str, Any]:
        d = self.to_dict(exclude_sensitive=False)
        d["display_name"] = self.display_name()
        d["is_archived"] = self.is_archived
        d["can_login"] = self.can_login()
        d["last_login_at_iso"] = (
            self.last_login_at.isoformat(timespec="seconds") if self.last_login_at else None
        )
        return d

    def anonymized_dict(self) -> Dict[str, Any]:
        d = self.to_dict(exclude_sensitive=True)
        email = d.get("email")
        if email:
            loc, _, dom = email.partition("@")
            d["email"] = (loc[:1] + "***@" + dom) if dom else "***"
        phone = d.get("phone")
        if phone:
            d["phone"] = "***" + phone[-3:] if len(phone) > 3 else "***"
        return d

    def get_login_methods(self) -> List[str]:
        methods = []
        if self.username:
            methods.append("username")
        if self.phone:
            methods.append("phone")
        if self.email:
            methods.append("email")
        return methods

    def get_last_login_delta(self) -> Optional[timedelta]:
        if not self.last_login_at:
            return None
        return utc_now() - self.last_login_at

    def get_failed_login_status(self) -> str:
        if self.failed_login_attempts == 0:
            return "No failed logins"
        if self.is_locked():
            return f"Locked until {self.locked_until}"
        return f"{self.failed_login_attempts} failed attempts"

    def ensure_has_any_identifier(self) -> None:
        if not (self.username or self.email or self.phone):
            raise ValueError("User must have at least one login identifier (username/email/phone).")

    # --------- Convenience / Admin operations ---------
    def transfer_company(self, company_id: Optional[int]) -> None:
        self.company_id = company_id
        self.modified_at = utc_now()

    def terminate_all_sessions(self) -> int:
        n = 0
        for s in list(self.sessions or []):
            if s.is_active:
                s.deactivate()
                n += 1
        return n

    def revoke_other_sessions(self, keep_session_id: Optional[int]) -> int:
        n = 0
        for s in list(self.sessions or []):
            if s.id != keep_session_id and s.is_active:
                s.deactivate()
                n += 1
        return n

    # ---------------- Relation helpers ----------------
    def get_last_audit_log(self):
        return self.audit_logs.order_by(
            func.coalesce(
                getattr(self.audit_logs.property.mapper.class_, "created_at", None), func.now()
            ).desc()
        ).first()

    def get_active_sessions(self) -> List["UserSession"]:
        return [s for s in self.sessions if s.is_active and not s.is_expired()]

    def get_current_otp(self, purpose: str = "login") -> Optional["OTPCode"]:
        valid_codes = [c for c in self.otp_codes if c.purpose == purpose and c.can_be_used()]
        return valid_codes[-1] if valid_codes else None

    def get_stock_movements(self, limit: int = 10) -> List["StockMovement"]:
        # без прямого импорта модели: берём класс из mapper связки
        mapper_cls = self.stock_movements.property.mapper.class_
        order_col = getattr(mapper_cls, "created_at", None) or getattr(
            mapper_cls, "timestamp", None
        )
        q = self.stock_movements
        if order_col is not None:
            q = q.order_by(order_col.desc())
        return q.limit(limit).all()

    def audit(self, action: str, meta: Optional[Dict] = None) -> None:
        # лениво создаём запись не импортируя класс
        payload: Dict[str, Any] = {
            "user_id": self.id,
            "action": action,
            "created_at": utc_now(),
        }
        log_cls = self.audit_logs.property.mapper.class_
        if hasattr(log_cls, "details"):
            payload["details"] = meta if isinstance(meta, dict) else (meta or {})
        elif hasattr(log_cls, "meta"):
            payload["meta"] = meta or {}
        else:
            payload["details"] = (
                meta if isinstance(meta, dict) else {"message": str(meta) if meta else ""}
            )
        log = log_cls(**payload)
        self.audit_logs.append(log)

    # ---------------- Highload locks ----------------
    @classmethod
    def locked_by_id(cls, session: Session, user_id: int) -> Optional["User"]:
        """SELECT ... FOR UPDATE по id — для безопасных мутаций балансов/флагов."""
        return for_update_by_id(session, cls, user_id)

    @classmethod
    def lock_advisory(cls, session: Session, lock_key: int) -> None:
        """Транзакционная advisory-блокировка; держится до COMMIT/ROLLBACK."""
        pg_advisory_xact_lock(session, lock_key)

    # ---------------- Query helpers (sync) ----------------
    @classmethod
    def find_by_identifier(cls, session: Session, identifier: str) -> Optional["User"]:
        if not identifier:
            return None
        ident = identifier.strip()
        q = session.query(cls).filter(
            or_(
                cls.username == ident,
                cls.phone == ident,
                func.lower(cls.email) == ident.lower(),
            )
        )
        return q.first()

    @classmethod
    def find_by_username(cls, session: Session, username: str) -> Optional["User"]:
        return session.query(cls).filter(cls.username == (username or "").strip()).first()

    @classmethod
    def find_by_email(cls, session: Session, email: str) -> Optional["User"]:
        return (
            session.query(cls)
            .filter(func.lower(cls.email) == (email or "").strip().lower())
            .first()
        )

    @classmethod
    def find_by_phone(cls, session: Session, phone: str) -> Optional["User"]:
        return session.query(cls).filter(cls.phone == (phone or "").strip()).first()

    @classmethod
    def search(
        cls, session: Session, q: str, company_id: Optional[int] = None, limit: int = 50
    ) -> List["User"]:
        query = session.query(cls).filter(cls.deleted_at.is_(None))
        if company_id is not None:
            query = query.filter(cls.company_id == company_id)
        if q:
            like = f"%{q.strip().lower()}%"
            query = query.filter(
                or_(
                    func.lower(cls.username).like(like),
                    func.lower(cls.email).like(like),
                    cls.phone.like(like),
                    func.lower(cls.full_name).like(like),
                )
            )
        return query.order_by(cls.created_at.desc()).limit(limit).all()

    @classmethod
    def get_or_create(
        cls,
        session: Session,
        *,
        username: Optional[str] = None,
        email: Optional[str] = None,
        phone: Optional[str] = None,
        defaults: Optional[Dict[str, Any]] = None,
    ) -> Tuple["User", bool]:
        user = None
        if username:
            user = session.query(cls).filter_by(username=username.strip()).first()
        if not user and email:
            user = session.query(cls).filter(func.lower(cls.email) == email.strip().lower()).first()
        if not user and phone:
            user = session.query(cls).filter_by(phone=phone.strip()).first()
        if user:
            return user, False
        data = {**(defaults or {})}
        if username:
            data["username"] = username.strip()
        if email:
            data["email"] = email.strip().lower()
        if phone:
            data["phone"] = phone.strip()
        user = cls(**data)
        session.add(user)
        session.commit()
        return user, True

    @classmethod
    def ensure_unique_identifiers(
        cls,
        session: Session,
        *,
        username: Optional[str],
        email: Optional[str],
        phone: Optional[str],
        exclude_id: Optional[int] = None,
    ) -> None:
        if username:
            q = session.query(cls).filter(cls.username == username.strip())
            if exclude_id:
                q = q.filter(cls.id != exclude_id)
            if session.query(q.exists()).scalar():
                raise ValueError("Username already taken")
        if email:
            e = email.strip().lower()
            q = session.query(cls).filter(func.lower(cls.email) == e)
            if exclude_id:
                q = q.filter(cls.id != exclude_id)
            if session.query(q.exists()).scalar():
                raise ValueError("Email already taken")
        if phone:
            p = phone.strip()
            q = session.query(cls).filter(cls.phone == p)
            if exclude_id:
                q = q.filter(cls.id != exclude_id)
            if session.query(q.exists()).scalar():
                raise ValueError("Phone already taken")

    # ---------------- Bulk operations (sync) ----------------
    @classmethod
    def bulk_soft_delete(cls, session: Session, ids: Iterable[int]) -> int:
        count = 0
        now = utc_now()
        for u in session.query(cls).filter(cls.id.in_(list(ids))).all():
            if not u.deleted_at:
                u.deleted_at = now
                u.is_active = False
                count += 1
        session.flush()
        return count

    @classmethod
    def bulk_unlock(cls, session: Session, ids: Iterable[int]) -> int:
        count = 0
        for u in session.query(cls).filter(cls.id.in_(list(ids))).all():
            if u.is_locked():
                u.unlock_user()
                count += 1
        session.flush()
        return count

    @classmethod
    def bulk_verify(cls, session: Session, ids: Iterable[int]) -> int:
        count = 0
        for u in session.query(cls).filter(cls.id.in_(list(ids))).all():
            if not u.is_verified:
                u.verify()
                count += 1
        session.flush()
        return count

    @classmethod
    def bulk_deactivate(cls, session: Session, ids: Iterable[int]) -> int:
        count = 0
        for u in session.query(cls).filter(cls.id.in_(list(ids))).all():
            if u.is_active and not u.is_superuser:
                u.deactivate()
                count += 1
        session.flush()
        return count

    @classmethod
    def bulk_activate(cls, session: Session, ids: Iterable[int]) -> int:
        count = 0
        for u in session.query(cls).filter(cls.id.in_(list(ids))).all():
            if not u.is_active and not u.is_archived:
                u.activate()
                count += 1
        session.flush()
        return count

    # ---------------- Async helpers (опционально) ----------------
    @classmethod
    async def a_find_by_identifier(cls, session, identifier: str) -> Optional["User"]:
        if not identifier:
            return None
        ident = identifier.strip()
        q = (
            select(cls)
            .where(
                or_(
                    cls.username == ident,
                    cls.phone == ident,
                    func.lower(cls.email) == ident.lower(),
                )
            )
            .limit(1)
        )
        res = await session.execute(q)
        return res.scalars().first()

    @classmethod
    async def a_search(
        cls, session, q: str, company_id: Optional[int] = None, limit: int = 50
    ) -> List["User"]:
        filters = [cls.deleted_at.is_(None)]
        if company_id is not None:
            filters.append(cls.company_id == company_id)
        if q:
            like = f"%{q.strip().lower()}%"
            filters.append(
                or_(
                    func.lower(cls.username).like(like),
                    func.lower(cls.email).like(like),
                    cls.phone.like(like),
                    func.lower(cls.full_name).like(like),
                )
            )
        stmt = select(cls).where(and_(*filters)).order_by(cls.created_at.desc()).limit(limit)
        res = await session.execute(stmt)
        return list(res.scalars().all())

    @classmethod
    async def a_get_or_create(
        cls,
        session,
        *,
        username: Optional[str] = None,
        email: Optional[str] = None,
        phone: Optional[str] = None,
        defaults: Optional[Dict[str, Any]] = None,
    ) -> Tuple["User", bool]:
        user: Optional["User"] = None
        if username:
            res = await session.execute(
                select(cls).where(cls.username == username.strip()).limit(1)
            )
            user = res.scalars().first()
        if not user and email:
            res = await session.execute(
                select(cls).where(func.lower(cls.email) == email.strip().lower()).limit(1)
            )
            user = res.scalars().first()
        if not user and phone:
            res = await session.execute(select(cls).where(cls.phone == phone.strip()).limit(1))
            user = res.scalars().first()
        if user:
            return user, False
        data = {**(defaults or {})}
        if username:
            data["username"] = username.strip()
        if email:
            data["email"] = email.strip().lower()
        if phone:
            data["phone"] = phone.strip()
        new_user = cls(**data)
        session.add(new_user)
        await session.commit()
        return new_user, True

    @classmethod
    async def a_bulk_deactivate(cls, session, ids: Iterable[int]) -> int:
        count = 0
        res = await session.execute(select(cls).where(cls.id.in_(list(ids))))
        for u in res.scalars().all():
            if u.is_active and not u.is_superuser:
                u.deactivate()
                count += 1
        await session.flush()
        return count

    @classmethod
    async def a_locked_by_id(cls, session, user_id: int) -> Optional["User"]:
        return await afor_update_by_id(session, cls, user_id)

    @classmethod
    async def a_lock_advisory(cls, session, lock_key: int) -> None:
        await apg_advisory_xact_lock(session, lock_key)

    # ---------------- Test factories ----------------
    @classmethod
    def factory(
        cls,
        *,
        username: Optional[str] = "testuser",
        email: Optional[str] = "test@example.com",
        phone: Optional[str] = "+70000000000",
        role: str = "manager",
        is_active: bool = True,
        is_verified: bool = False,
        company_id: Optional[int] = None,
        full_name: Optional[str] = "Test User",
    ) -> "User":
        return cls(
            username=username,
            email=email.lower() if email else None,
            phone=phone,
            role=role,
            is_active=is_active,
            is_verified=is_verified,
            company_id=company_id,
            full_name=full_name,
            hashed_password="",
        )


# ======================================================================================
# UserSession
# ======================================================================================
DEFAULT_SESSION_TTL_MIN = 60 * 24 * 30  # 30 days

class UserSession(LenientInitMixin, BaseModel, SoftDeleteMixin):
    __tablename__ = "user_sessions"
    __allow_unmapped__ = True

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    refresh_token = Column(String(255), unique=True, nullable=False)
    ip_address = Column(String(45), nullable=True)
    user_agent = Column(String(500), nullable=True)

    # ВАЖНО: безопасный дефолт. Даже если инстанс создан без expires_at — слушатели добавят значение.
    expires_at = Column(
        DateTime,
        nullable=False,
        default=lambda: utc_now() + timedelta(minutes=DEFAULT_SESSION_TTL_MIN),
    )

    is_active = Column(Boolean, nullable=False, default=True, server_default=text("true"))
    created_at = Column(DateTime, nullable=False, default=utc_now, server_default=func.now())
    terminated_at = Column(DateTime, nullable=True)

    # Из миксинов придут updated_at / deleted_at — не трогаем контракт.
    user = relationship("User", back_populates="sessions", lazy="joined")

    __table_args__ = (
        Index("ix_user_sessions_active_user", "is_active", "user_id"),
        Index("ix_user_sessions_user_expires", "user_id", "expires_at"),
    )

    @validates("expires_at", "created_at", "terminated_at")
    def _coerce_dt(self, _key, value):
        return _parse_dt(value)

    def __repr__(self) -> str:
        return f"<UserSession(id={self.id}, user_id={self.user_id}, active={self.is_active})>"

    # ----- Derived -----
    def is_expired(self) -> bool:
        return utc_now() > self.expires_at

    @property
    def seconds_left(self) -> int:
        if not self.expires_at:
            return 0
        return max(int((self.expires_at - utc_now()).total_seconds()), 0)

    # ----- Mutations -----
    def deactivate(self) -> None:
        self.is_active = False
        self.terminated_at = utc_now()

    def get_user(self) -> User:
        return self.user

    def to_dict(self, exclude_sensitive: bool = False) -> dict:
        data = {col.name: getattr(self, col.name) for col in self.__table__.columns}
        if exclude_sensitive:
            data.pop("refresh_token", None)
        return data

    @classmethod
    def start_new(
        cls, user_id: int, refresh_token: str, ttl_minutes: int = 60 * 24 * 7
    ) -> "UserSession":
        return cls(
            user_id=user_id,
            refresh_token=refresh_token,
            expires_at=utc_now() + timedelta(minutes=ttl_minutes),
            is_active=True,
        )

    def session_status(self) -> str:
        if not self.is_active:
            return "terminated"
        if self.is_expired():
            return "expired"
        return "active"

    def session_age(self) -> float:
        return (utc_now() - self.created_at).total_seconds()

    def expire_now(self) -> None:
        self.expires_at = utc_now()
        self.is_active = False
        self.terminated_at = utc_now()

    def renew(self, ttl_minutes: int) -> None:
        self.expires_at = utc_now() + timedelta(minutes=ttl_minutes)
        self.is_active = True
        self.terminated_at = None

    # Test factory
    @classmethod
    def factory(
        cls,
        *,
        user_id: int,
        ttl_minutes: int = 60 * 24 * 7,
        ip: str = "127.0.0.1",
        ua: str = "pytest",
    ) -> "UserSession":
        return cls(
            user_id=user_id,
            refresh_token=f"rt-{user_id}-{int(utc_now().timestamp())}",
            ip_address=ip,
            user_agent=ua,
            expires_at=utc_now() + timedelta(minutes=ttl_minutes),
            is_active=True,
        )

    # ----- Internal safety (используется слушателями) -----
    def _ensure_defaults_on_session(self) -> None:
        """
        Гарантированно выставляет корректные значения по умолчанию.
        Используется в init/before_insert/before_update.
        """
        if getattr(self, "created_at", None) is None:
            self.created_at = utc_now()
        if getattr(self, "expires_at", None) is None:
            self.expires_at = utc_now() + timedelta(minutes=DEFAULT_SESSION_TTL_MIN)
        if getattr(self, "is_active", None) is None:
            self.is_active = True


# ---- UserSession listeners (надежно и идемпотентно) ----
def _usersession_init_fn(target, args, kwargs) -> None:
    try:
        target._ensure_defaults_on_session()
    except Exception:
        # не ломаем создание из-за аудита/миксов
        if getattr(target, "expires_at", None) is None:
            target.expires_at = utc_now() + timedelta(minutes=DEFAULT_SESSION_TTL_MIN)
        if getattr(target, "created_at", None) is None:
            target.created_at = utc_now()
        if getattr(target, "is_active", None) is None:
            target.is_active = True


def _usersession_before_insert_fn(_mapper, _connection, target: UserSession) -> None:
    target._ensure_defaults_on_session()
    # обновление updated_at, если миксины его предоставляют
    if hasattr(target, "updated_at"):
        setattr(target, "updated_at", utc_now())


def _usersession_before_update_fn(_mapper, _connection, target: UserSession) -> None:
    # не даём expires_at оказаться None
    if getattr(target, "expires_at", None) is None:
        target.expires_at = utc_now() + timedelta(minutes=DEFAULT_SESSION_TTL_MIN)
    if hasattr(target, "updated_at"):
        setattr(target, "updated_at", utc_now())


# Регистрируем только если ещё не было (защита от двойной регистрации при повторных импортам)
if not event.contains(UserSession, "init", _usersession_init_fn):
    event.listen(UserSession, "init", _usersession_init_fn, propagate=True)
if not event.contains(UserSession, "before_insert", _usersession_before_insert_fn):
    event.listen(UserSession, "before_insert", _usersession_before_insert_fn, propagate=True)
if not event.contains(UserSession, "before_update", _usersession_before_update_fn):
    event.listen(UserSession, "before_update", _usersession_before_update_fn, propagate=True)


# ======================================================================================
# OTPCode
# ======================================================================================
class OTPCode(LenientInitMixin, BaseModel):
    __tablename__ = "otp_codes"
    __allow_unmapped__ = True

    id = Column(Integer, primary_key=True, index=True)
    phone = Column(String(20), nullable=False, index=True)
    code = Column(String(6), nullable=False)
    purpose = Column(String(50), nullable=False)  # 'registration', 'login', 'reset'
    expires_at = Column(DateTime, nullable=False)
    is_used = Column(Boolean, nullable=False, default=False, server_default=text("false"))
    attempts = Column(Integer, nullable=False, default=0, server_default=text("0"))
    user_id = Column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    created_at = Column(DateTime, nullable=False, default=utc_now, server_default=func.now())
    verified_at = Column(DateTime, nullable=True)

    user = relationship("User", back_populates="otp_codes", foreign_keys=[user_id], lazy="joined")

    __table_args__ = (
        Index("ix_otp_phone_purpose", "phone", "purpose"),
        Index("ix_otp_user_purpose", "user_id", "purpose"),
        CheckConstraint("attempts >= 0", name="ck_otp_attempts_nonneg"),
        UniqueConstraint("code", "phone", "purpose", name="uq_otp_code_phone_purpose"),
    )

    @validates("phone")
    def _validate_phone(self, _key, value: str):
        v = value.strip() if isinstance(value, str) else value
        if v and not PHONE_REGEX.match(v):
            raise ValueError("Invalid phone format")
        return v

    @validates("code")
    def _digits_code(self, _key, value: str):
        v = value.strip() if isinstance(value, str) else value
        if not v or len(v) != OTP_LENGTH or not v.isdigit():
            raise ValueError(f"OTP code must be exactly {OTP_LENGTH} digits")
        return v

    @validates("expires_at", "created_at", "verified_at")
    def _coerce_dt(self, _key, value):
        return _parse_dt(value)

    def __repr__(self) -> str:
        return f"<OTPCode(id={self.id}, phone={self.phone}, used={self.is_used}, attempts={self.attempts})>"

    # ----- State mutations -----
    def mark_as_used(self) -> None:
        self.is_used = True
        self.verified_at = utc_now()

    def increment_attempts(self) -> None:
        self.attempts += 1

    # ----- Derived state -----
    def is_expired(self) -> bool:
        return utc_now() > self.expires_at

    def seconds_to_expiry(self) -> Optional[int]:
        if self.expires_at is None:
            return None
        delta = self.expires_at - utc_now()
        return max(int(delta.total_seconds()), 0)

    def can_be_used(self) -> bool:
        return not self.is_used and not self.is_expired() and self.attempts < OTP_MAX_ATTEMPTS

    # ----- Convenience -----
    def get_user(self) -> Optional[User]:
        return self.user

    def to_dict(self) -> dict:
        return {col.name: getattr(self, col.name) for col in self.__table__.columns}

    def otp_status(self) -> str:
        if self.is_used:
            return "used"
        if self.is_expired():
            return "expired"
        return "valid"

    def attempts_left(self, max_attempts: int = OTP_MAX_ATTEMPTS) -> int:
        return max(0, max_attempts - self.attempts)

    def is_for_login(self) -> bool:
        return self.purpose == "login"

    def is_for_registration(self) -> bool:
        return self.purpose == "registration"

    def is_for_reset(self) -> bool:
        return self.purpose == "reset"

    @classmethod
    def cleanup_expired(cls, session: Session, older_than_minutes: int = OTP_EXPIRY_MINUTES) -> int:
        """
        Удаление просроченных кодов, а также давно использованных (старше older_than_minutes).
        Возвращает количество удалённых записей.
        """
        threshold = utc_now() - timedelta(minutes=older_than_minutes)
        q = session.query(cls).filter(
            or_(
                cls.expires_at < threshold,
                and_(cls.is_used.is_(True), cls.created_at < threshold),
            )
        )
        rows = q.all()
        for row in rows:
            session.delete(row)
        session.flush()
        return len(rows)

    @classmethod
    def generate_code(cls, phone: str, purpose: str, user_id: Optional[int] = None) -> "OTPCode":
        from random import randint

        code = str(randint(10 ** (OTP_LENGTH - 1), 10**OTP_LENGTH - 1)).zfill(OTP_LENGTH)
        expires_at = utc_now() + timedelta(minutes=OTP_EXPIRY_MINUTES)
        return cls(
            phone=phone,
            code=code,
            purpose=purpose,
            expires_at=expires_at,
            is_used=False,
            attempts=0,
            user_id=user_id,
            created_at=utc_now(),
        )

    def validate_for_use(self, phone: str, code: str, purpose: str) -> bool:
        if self.phone != phone or self.code != code or self.purpose != purpose:
            return False
        if self.is_expired() or self.is_used or self.attempts >= OTP_MAX_ATTEMPTS:
            return False
        return True

    def use_attempt(self) -> bool:
        self.increment_attempts()
        if self.can_be_used():
            self.mark_as_used()
            return True
        return False

    # ----- External integrations (stubs) -----
    def send_sms(self) -> None:
        # TODO: integrate with SMS provider
        pass

    def send_email(self) -> None:
        # TODO: integrate with Email provider
        pass

    def notify_user(self) -> None:
        # TODO: Implement notification logic (SMS or email)
        pass

    # Test factory
    @classmethod
    def factory(
        cls,
        *,
        phone: str = "+70000000001",
        purpose: str = "login",
        user_id: Optional[int] = None,
        expires_in_minutes: int = OTP_EXPIRY_MINUTES,
    ) -> "OTPCode":
        from random import randint

        code = str(randint(10 ** (OTP_LENGTH - 1), 10**OTP_LENGTH - 1)).zfill(OTP_LENGTH)
        return cls(
            phone=phone,
            code=code,
            purpose=purpose,
            expires_at=utc_now() + timedelta(minutes=expires_in_minutes),
            is_used=False,
            attempts=0,
            user_id=user_id,
            created_at=utc_now(),
        )


# ======================================================================================
# Events / Normalizers (soft, не ломают тестовые данные)
# ======================================================================================
@event.listens_for(User, "before_insert")
def _user_before_insert(mapper, connection, target: User):  # pragma: no cover
    # Мягкая нормализация
    if target.email:
        target.email = target.email.strip().lower()
    if target.username:
        target.username = target.username.strip()
    if target.phone:
        target.phone = target.phone.strip()
    # Автоверификация по корпоративным доменам (по желанию)
    if target.email and any(target.email.endswith(dom) for dom in TRUSTED_EMAIL_DOMAINS):
        target.is_verified = True


@event.listens_for(User, "before_update")
def _user_before_update(mapper, connection, target: User):  # pragma: no cover
    if target.email:
        target.email = target.email.strip().lower()
    if target.username:
        target.username = target.username.strip()
    if target.phone:
        target.phone = target.phone.strip()


# ======================================================================================
# Exports
# ======================================================================================
__all__ = ["User", "UserSession", "OTPCode"]
