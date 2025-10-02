# app/models/audit.py
"""
Audit log model for tracking user actions and system events.
Production best practices:
- Multi-tenant, audit, extensible
- Track all entity actions (user, product, order, warehouse, company)
- UTC naive timestamps (server_default=func.now())
- All changes, context, relations, request/session info, error handling
- Full CRUD, serialization, masking, summary, change-tracking, filtering
- Customizable PII masking via hooks
- High-performance bulk insert
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional, Dict, Any, List, Generator, Iterable, Callable

from sqlalchemy import JSON, Integer, String, Text, Index, ForeignKey, func
from sqlalchemy.orm import Session, Mapped, mapped_column, relationship

from app.models.base import BaseModel, TenantMixin, AuditMixin

# ---------------------------------------------------------------------
# Тип для пользовательских маскировщиков PII (выполняются в on_pre_commit)
# ---------------------------------------------------------------------
Masker = Callable[["AuditLog"], None]


class AuditLog(BaseModel, TenantMixin, AuditMixin):
    """
    Аудит-событие.

    Базовые принципы:
    - Храним максимум контекста (кто/что/где/когда/зачем).
    - Для предотвращения циклов импортов и перегрева ORM — минимизируем внешние связи.
      Исключение: явные FK на users/products/warehouses, чтобы корректно работали
      отношения User.audit_logs, Product.audit_logs и Warehouse.audit_logs.
    """

    __tablename__ = "audit_logs"
    __allow_unmapped__ = True

    # -------------------- Контекст «кто/что» --------------------
    action: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    description: Mapped[Optional[str]] = mapped_column(Text)

    # Ключевые идентификаторы + FK для корректной ORM-связи
    user_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        doc="FK на пользователя-инициатора (может быть NULL для системных событий).",
    )
    company_id: Mapped[Optional[int]] = mapped_column(Integer, index=True)

    # Связь с Warehouse (односторонняя в вашем warehouse.py через backref)
    warehouse_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("warehouses.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        doc="Связь с Warehouse. Нужна для корректной relationship Warehouse.audit_logs.",
    )

    # Для корректной двухсторонней связи с Product (Product.audit_logs)
    product_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
        doc="FK на Product: удобно для аналитики и каскадного удаления.",
    )

    # Остальные ссылки — без жёстких FK (чтобы не плодить зависимости)
    order_id: Mapped[Optional[int]] = mapped_column(Integer, index=True)
    payment_id: Mapped[Optional[int]] = mapped_column(Integer, index=True)

    # Полиморфная ссылка на произвольную сущность
    entity_type: Mapped[Optional[str]] = mapped_column(String(50), index=True)
    entity_id: Mapped[Optional[int]] = mapped_column(Integer, index=True)

    # Снапшоты изменений (PII маскируются)
    old_values: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSON)
    new_values: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSON)

    # Запрос/сессия (маскируется в публичной выдаче)
    ip_address: Mapped[Optional[str]] = mapped_column(String(45))
    user_agent: Mapped[Optional[str]] = mapped_column(Text)
    request_id: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    correlation_id: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    source: Mapped[Optional[str]] = mapped_column(String(64), index=True)  # api/worker/webhook…

    # Произвольные детали/расширения (например, статусы внешних интеграций)
    details: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSON)

    # -------------------- ORM relationships --------------------
    # ВАЖНО: back_populates должен совпадать с тем, что задано в User.audit_logs
    user = relationship(
        "User",
        back_populates="audit_logs",
        foreign_keys=[user_id],
        lazy="selectin",
    )
    # Если в модели Product есть `audit_logs = relationship("AuditLog", back_populates="product", ...)`,
    # то используем back_populates. Это безопасно и не создаёт автогенерации с дубликатами.
    product = relationship(
        "Product",
        back_populates="audit_logs",
        foreign_keys=[product_id],
        lazy="selectin",
    )
    # Связь с Warehouse задана через backref на стороне Warehouse (см. warehouse.py)

    __table_args__ = (
        Index("ix_audit_entity_type_id", "entity_type", "entity_id"),
        Index("ix_audit_action_user", "action", "user_id"),
        Index("ix_audit_created_at", "created_at"),
        Index("ix_audit_wh_created", "warehouse_id", "created_at"),
    )

    # --------------------- Реестр маскировщиков ---------------------
    MASKERS: List[Masker] = []

    @classmethod
    def register_masker(cls, fn: Masker) -> None:
        """Зарегистрировать кастомный маскировщик для on_pre_commit()."""
        if fn not in cls.MASKERS:
            cls.MASKERS.append(fn)

    @classmethod
    def clear_maskers(cls) -> None:
        """Очистить реестр маскировщиков (удобно в тестах)."""
        cls.MASKERS.clear()

    # --------------------- CRUD / создание ---------------------
    @classmethod
    def create_log(
        cls,
        action: str,
        session: Optional[Session] = None,
        *,
        description: Optional[str] = None,
        user_id: Optional[int] = None,
        company_id: Optional[int] = None,
        warehouse_id: Optional[int] = None,
        product_id: Optional[int] = None,
        order_id: Optional[int] = None,
        payment_id: Optional[int] = None,
        entity_type: Optional[str] = None,
        entity_id: Optional[int] = None,
        old_values: Optional[Dict[str, Any]] = None,
        new_values: Optional[Dict[str, Any]] = None,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
        request_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
        source: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
        last_modified_by: Optional[int] = None,
        commit: bool = True,
    ) -> "AuditLog":
        if not action or not action.strip():
            raise ValueError("action cannot be empty")
        obj = cls(
            action=action.strip()[:100],
            description=description,
            user_id=user_id,
            company_id=company_id,
            warehouse_id=warehouse_id,
            product_id=product_id,
            order_id=order_id,
            payment_id=payment_id,
            entity_type=(entity_type.strip().lower() if entity_type else None),
            entity_id=entity_id,
            old_values=old_values,
            new_values=new_values,
            ip_address=ip_address,
            user_agent=user_agent,
            request_id=request_id,
            correlation_id=correlation_id,
            source=source,
            details=details,
            last_modified_by=last_modified_by,
        )

        # Хук до коммита — маскируем PII
        try:
            obj.on_pre_commit()
        except Exception:
            pass

        if session is not None:
            session.add(obj)
            if commit:
                session.commit()
            try:
                obj.on_post_commit()
            except Exception:
                pass

        return obj

    @classmethod
    def log_action(cls, session: Session, action: str, **kwargs) -> "AuditLog":
        """Короткий алиас — создание с требуемым session."""
        return cls.create_log(action=action, session=session, **kwargs)

    @classmethod
    def safe_log(cls, session: Optional[Session], action: str, **kwargs) -> Optional["AuditLog"]:
        """Безопасный лог — не бросает исключений, откатывает транзакцию при ошибке."""
        try:
            return cls.create_log(action=action, session=session, **kwargs)
        except Exception:
            if session is not None:
                try:
                    session.rollback()
                except Exception:
                    pass
            return None

    @classmethod
    def bulk_insert(
        cls, session: Session, logs: Iterable[Dict[str, Any]], commit: bool = True
    ) -> int:
        """Простая пакетная вставка через add (макс. совместимость)."""
        cnt = 0
        for data in logs:
            obj = cls(**data)
            try:
                obj.on_pre_commit()
            except Exception:
                pass
            session.add(obj)
            cnt += 1
        if commit:
            session.commit()
        # post-commit хуки намеренно без привязки (best-effort)
        return cnt

    @classmethod
    def batch_insert_fast(
        cls,
        session: Session,
        logs: Iterable[Dict[str, Any]],
        *,
        return_defaults: bool = False,
        preserve_order: bool = True,
        commit: bool = True,
    ) -> int:
        """
        Быстрый bulk через bulk_save_objects (экономит память и ускоряет вставку).
        """
        objs = [cls(**d) for d in logs]
        for obj in objs:
            try:
                obj.on_pre_commit()
            except Exception:
                pass
        try:
            session.bulk_save_objects(
                objs, return_defaults=return_defaults, preserve_order=preserve_order
            )
            if commit:
                session.commit()
        except Exception:
            try:
                session.rollback()
            except Exception:
                pass
            raise
        for obj in objs:
            try:
                obj.on_post_commit()
            except Exception:
                pass
        return len(objs)

    # --------------------- Поиски/фильтры ---------------------
    @classmethod
    def get_by_id(cls, session: Session, log_id: int) -> Optional["AuditLog"]:
        return session.query(cls).filter_by(id=log_id).first()

    @classmethod
    def get_all(cls, session: Session, limit: int = 100) -> List["AuditLog"]:
        return session.query(cls).order_by(cls.created_at.desc()).limit(limit).all()

    @classmethod
    def find_by_entity(
        cls, session: Session, entity_type: str, entity_id: int, limit: int = 100
    ) -> List["AuditLog"]:
        return (
            session.query(cls)
            .filter_by(entity_type=entity_type.strip().lower(), entity_id=entity_id)
            .order_by(cls.created_at.desc())
            .limit(limit)
            .all()
        )

    @classmethod
    def find_by_action(cls, session: Session, action: str, limit: int = 100) -> List["AuditLog"]:
        return (
            session.query(cls)
            .filter_by(action=action.strip()[:100])
            .order_by(cls.created_at.desc())
            .limit(limit)
            .all()
        )

    @classmethod
    def find_by_user(cls, session: Session, user_id: int, limit: int = 100) -> List["AuditLog"]:
        return (
            session.query(cls)
            .filter_by(user_id=user_id)
            .order_by(cls.created_at.desc())
            .limit(limit)
            .all()
        )

    @classmethod
    def filter_by_period(
        cls, session: Session, start: datetime, end: datetime, limit: int = 100
    ) -> List["AuditLog"]:
        return (
            session.query(cls)
            .filter(cls.created_at >= start, cls.created_at <= end)
            .order_by(cls.created_at.desc())
            .limit(limit)
            .all()
        )

    @classmethod
    def filter_by_context(
        cls,
        session: Session,
        *,
        company_id: Optional[int] = None,
        warehouse_id: Optional[int] = None,
        product_id: Optional[int] = None,
        order_id: Optional[int] = None,
        payment_id: Optional[int] = None,
        limit: int = 100,
    ) -> List["AuditLog"]:
        q = session.query(cls)
        if company_id is not None:
            q = q.filter_by(company_id=company_id)
        if warehouse_id is not None:
            q = q.filter_by(warehouse_id=warehouse_id)
        if product_id is not None:
            q = q.filter_by(product_id=product_id)
        if order_id is not None:
            q = q.filter_by(order_id=order_id)
        if payment_id is not None:
            q = q.filter_by(payment_id=payment_id)
        return q.order_by(cls.created_at.desc()).limit(limit).all()

    @classmethod
    def batch_export(
        cls, session: Session, limit: int = 1000
    ) -> Generator[Dict[str, Any], None, None]:
        """Экспорт логов (маскируем PII в публичной части)."""
        for obj in session.query(cls).order_by(cls.created_at.desc()).limit(limit).yield_per(200):
            yield obj.to_public_dict()

    @classmethod
    def batch_delete(cls, session: Session, before: datetime) -> int:
        """Удалить все логи, созданные раньше `before`."""
        deleted = session.query(cls).filter(cls.created_at < before).delete()
        session.commit()
        return deleted

    # --------------------- Сериализация ---------------------
    def __repr__(self) -> str:  # pragma: no cover
        return f"<AuditLog(id={self.id}, action='{self.action}', user_id={self.user_id})>"

    def set_modified_by(self, user_id: int) -> None:
        self.last_modified_by = user_id

    def to_dict(self, hide_sensitive: bool = True) -> Dict[str, Any]:
        """Полная сериализация (при hide_sensitive убираем PII)."""
        data = {c.name: getattr(self, c.name) for c in self.__table__.columns}
        if hide_sensitive:
            data.pop("ip_address", None)
            data.pop("user_agent", None)
        return data

    def to_public_dict(self) -> Dict[str, Any]:
        """Безопасная выдача наружу (API/экспорт)."""
        return {
            "id": self.id,
            "action": self.action,
            "description": self.description,
            "user_id": self.user_id,
            "company_id": self.company_id,
            "warehouse_id": self.warehouse_id,
            "product_id": self.product_id,
            "order_id": self.order_id,
            "payment_id": self.payment_id,
            "entity_type": self.entity_type,
            "entity_id": self.entity_id,
            "old_values": self.old_values,
            "new_values": self.new_values,
            "request_id": self.request_id,
            "correlation_id": self.correlation_id,
            "source": self.source,
            "details": self.details,
            "created_at": self.created_at,
        }

    def as_summary(self) -> Dict[str, Any]:
        """Короткое представление для UI/списков."""
        return {
            "id": self.id,
            "action": self.action,
            "user_id": self.user_id,
            "entity_type": self.entity_type,
            "entity_id": self.entity_id,
            "timestamp": self.created_at,
            "description": self.description,
        }

    def get_changes(self) -> Dict[str, Dict[str, Any]]:
        """Возвратить только отличающиеся поля между old/new."""
        changes: Dict[str, Dict[str, Any]] = {}
        if self.old_values and self.new_values:
            for k, new_val in self.new_values.items():
                old_val = self.old_values.get(k)
                if old_val != new_val:
                    changes[k] = {"old": old_val, "new": new_val}
        return changes

    # --------------------- Security / PII masking / hooks ---------------------
    def mask_sensitive(self) -> None:
        """Убираем PII — пригодно для GDPR и публичных API."""
        self.ip_address = None
        self.user_agent = None

    def anonymize(self) -> None:
        """Полная анонимизация (при необходимости соблюдения GDPR)."""
        self.mask_sensitive()
        self.user_id = None
        self.company_id = None
        self.details = None
        self.old_values = None
        self.new_values = None

    def on_pre_commit(self) -> None:
        """Hook перед коммитом — базовое маскирование + кастомные маскировщики."""

        def _mask_val(v: Any) -> Any:
            if not isinstance(v, str):
                return v
            lower = v.lower()
            if any(
                k in lower
                for k in ("password", "secret", "token", "apikey", "api_key", "authorization")
            ):
                return "***"
            return v

        if isinstance(self.details, dict):
            self.details = {k: _mask_val(v) for k, v in self.details.items()}

        for fn in self.MASKERS:
            try:
                fn(self)
            except Exception:
                # маскировщик не должен ронять транзакцию
                continue

    def on_post_commit(self) -> None:
        """Hook после коммита — здесь можно отослать alert/webhook в SIEM."""
        # noop by default (интеграция делается на уровне сервисов/репозиториев)
        pass

    # --------------------- Утилиты для алёртинга/SIEM ---------------------
    def to_alert(self) -> Dict[str, Any]:
        """Минимальный payload для алёртинга/SIEM/Kafka/Sentry/ELK."""
        return {
            "id": self.id,
            "action": self.action,
            "description": self.description,
            "user_id": self.user_id,
            "entity_type": self.entity_type,
            "entity_id": self.entity_id,
            "company_id": self.company_id,
            "order_id": self.order_id,
            "payment_id": self.payment_id,
            "request_id": self.request_id,
            "correlation_id": self.correlation_id,
            "source": self.source,
            "timestamp": self.created_at,
        }

    # --------------------- Сопоставление ---------------------
    def is_for_entity(self, entity_type: Optional[str], entity_id: Optional[int]) -> bool:
        return self.entity_type == (
            entity_type.strip().lower() if entity_type else None
        ) and self.entity_id == (entity_id if entity_id is not None else None)

    def short_description(self) -> str:
        return f"{self.action}: {self.entity_type}({self.entity_id}) user={self.user_id}"


class AuditAction:
    """Константы событий + краткие описания (для автодоков/аналитики)."""

    # User actions
    USER_LOGIN = "user_login"
    USER_LOGOUT = "user_logout"
    USER_REGISTER = "user_register"
    USER_UPDATE = "user_update"
    USER_DELETE = "user_delete"

    # Product actions
    PRODUCT_CREATE = "product_create"
    PRODUCT_UPDATE = "product_update"
    PRODUCT_DELETE = "product_delete"
    PRODUCT_IMPORT = "product_import"
    PRODUCT_EXPORT = "product_export"

    # Order actions
    ORDER_CREATE = "order_create"
    ORDER_UPDATE = "order_update"
    ORDER_CANCEL = "order_cancel"
    ORDER_COMPLETE = "order_complete"

    # Payment actions
    PAYMENT_CREATE = "payment_create"
    PAYMENT_SUCCESS = "payment_success"
    PAYMENT_FAIL = "payment_fail"
    PAYMENT_REFUND = "payment_refund"

    # Stock actions
    STOCK_IN = "stock_in"
    STOCK_OUT = "stock_out"
    STOCK_TRANSFER = "stock_transfer"
    STOCK_ADJUSTMENT = "stock_adjustment"

    # System actions
    WEBHOOK_RECEIVED = "webhook_received"
    SYSTEM_ERROR = "system_error"
    BACKUP_CREATED = "backup_created"

    # Extra: Custom audit actions
    CUSTOM = "custom"

    ACTION_DOCS: Dict[str, str] = {
        USER_LOGIN: "User successfully logged in.",
        USER_LOGOUT: "User logged out.",
        USER_REGISTER: "New user registration.",
        USER_UPDATE: "User profile or settings changed.",
        USER_DELETE: "User deleted or archived.",
        PRODUCT_CREATE: "Product created.",
        PRODUCT_UPDATE: "Product updated.",
        PRODUCT_DELETE: "Product deleted or archived.",
        PRODUCT_IMPORT: "Products imported from external source.",
        PRODUCT_EXPORT: "Products exported to external system/file.",
        ORDER_CREATE: "Order created.",
        ORDER_UPDATE: "Order updated.",
        ORDER_CANCEL: "Order cancelled.",
        ORDER_COMPLETE: "Order completed/fulfilled.",
        PAYMENT_CREATE: "Payment initiated/created.",
        PAYMENT_SUCCESS: "Payment successfully processed.",
        PAYMENT_FAIL: "Payment failed.",
        PAYMENT_REFUND: "Payment refunded.",
        STOCK_IN: "Stock received (inbound).",
        STOCK_OUT: "Stock shipped (outbound).",
        STOCK_TRANSFER: "Stock transferred between locations.",
        STOCK_ADJUSTMENT: "Manual stock adjustment.",
        WEBHOOK_RECEIVED: "External webhook received.",
        SYSTEM_ERROR: "System-level error captured.",
        BACKUP_CREATED: "Backup created.",
        CUSTOM: "Custom audit action.",
    }

    @classmethod
    def get_action_doc(cls, action: str) -> Optional[str]:
        return cls.ACTION_DOCS.get(action)

    @classmethod
    def all_action_docs(cls) -> Dict[str, str]:
        return dict(cls.ACTION_DOCS)


__all__ = ["AuditLog", "AuditAction", "Masker"]
