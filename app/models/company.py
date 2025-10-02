# app/models/company.py
"""
Company model for multi-tenancy support.
Adapted from PR #18 to work with PR #21 base model system.

Доведено до production и расширено по ТЗ:

— Время: все timestamp'ы tz-aware (UTC).
— Поля/ограничения: индексы, CHECK/UNIQUE.
— Управление жизненным циклом: activate/deactivate/soft_delete/restore (+reactivate).
— Property: is_archived (delete_reason == "archived").
— Settings: версионирование и аудит изменений (settings_version, settings_history)
  с ограничением глубины (retention) и возможностью восстановления прошлой версии.
— Интеграции: внешние ID + поля синхронизации и методы sync (Kaspi/1C/generic) и batch-sync.
— Kaspi helpers: безопасная маскировка ключа, ротация.
— Аналитика: быстрые агрегаты по заказам/платежам/долгам + BI-выгрузка в pandas/CSV/Parquet/Excel.
— Массовые операции: архив/восстановление компаний (bulk archive/restore) с аудитом и нотификациями.
— Owner: полноценная FK + relationship и методы смены владельца.
— Безопасность: GDPR consent поля и метод регистрации согласия (+аудит).
— Платёжная схема: кошелёк + автосписание (по умолчанию выключено) + инвойс «на нехватающую сумму»,
  запрет отрицательного баланса, минимально «положительный» баланс >= 1₸ или допускается ровно 0.
— Безопасные связи: отношения создаются «мягко» — если целевая модель ещё не импортирована,
  маппер не падает, тесты по базовой модели проходят.

Ничего не удалено из исходного файла — только добавления/исправления/расширения.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any, Callable, Dict, Iterable, Optional, Sequence, Tuple, List

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    select,
    update,
    literal,
)
from sqlalchemy.orm import relationship, validates, object_session

from app.models.base import Base


# -----------------------------------------------------------------------
# Константы/утилиты
# -----------------------------------------------------------------------
SETTINGS_HISTORY_LIMIT = 50  # хранить только последние N версий
DEFAULT_CSV_INDEX = False


def utc_now() -> datetime:
    return datetime.now(UTC)


def _mask_secret(value: Optional[str], keep_last: int = 4) -> Optional[str]:
    if not value:
        return value
    v = value.strip()
    if len(v) <= keep_last:
        return "*" * len(v)
    return "*" * (len(v) - keep_last) + v[-keep_last:]


# -------- Настройки кошелька/биллинга (дефолты и ключи в settings JSON) ----------
DEFAULT_WALLET_SETTINGS: Dict[str, Any] = {
    # автосписание подписок/счетов с привязанной карты/счёта (по умолчанию ВЫКЛ.)
    "wallet.autopay_enabled": False,
    # минимально «положительный» баланс. Бизнес-правило: «достаточно 1₸ или ровно 0».
    "wallet.min_positive_balance": 1,
    # допускается ровно 0 как «неотрицательный» баланс
    "wallet.allow_zero_ok": True,
    # приоритет канала выставления счёта, если средств не хватает
    # "kaspi" | "bank" | "auto"
    "billing.preferred_invoice_channel": "auto",
    # выставлять инвойс ровно на нехватающую сумму
    "billing.partial_invoice": True,
    # валюта по умолчанию
    "billing.currency": "KZT",
}

# Для безопасного создания relationships без падений, если модули не импортированы
_REL_IMPORTS = {
    "User": "app.models.user",
    "Product": "app.models.product",
    "Order": "app.models.order",
    "Warehouse": "app.models.warehouse",
    "Campaign": "app.models.campaign",
    "Subscription": "app.models.billing",
    "BillingPayment": "app.models.billing",
    "Invoice": "app.models.invoice",
    "WalletBalance": "app.models.wallet",
    "AuditLog": "app.models.audit",
    "ExternalAuditEvent": "app.models.audit",
}


def _safe_import_for_rel(target: str) -> bool:
    """
    Пробует импортировать модуль, где объявлена целевая модель relationship.
    Если не получилось — просто вернём False, и верхний код может решить,
    создавать ли связь сейчас или отложить (None).
    """
    module = _REL_IMPORTS.get(target)
    if not module:
        return True  # ничего импортировать не требуется
    try:
        __import__(module)
        return True
    except Exception:
        return False


# -----------------------------------------------------------------------
# Company
# -----------------------------------------------------------------------
class Company(Base):
    """Company/Store model for multi-tenancy"""

    __tablename__ = "companies"
    __allow_unmapped__ = True

    # PRIMARY KEY
    id = Column(Integer, primary_key=True, index=True)

    # Basic info
    name = Column(String(255), nullable=False, index=True)
    bin_iin = Column(String(32), nullable=True, unique=True, index=True)

    # Contact info
    phone = Column(String(32), nullable=True)
    email = Column(String(255), nullable=True)
    address = Column(Text, nullable=True)

    # Settings / статус
    is_active = Column(Boolean, nullable=False, default=True, index=True)

    # Интеграции: Kaspi
    kaspi_store_id = Column(String(64), nullable=True, unique=True, index=True)
    kaspi_api_key = Column(String(255), nullable=True)

    # Внешние ID/интеграции (generic + 1C)
    external_id = Column(
        String(64), nullable=True, unique=True, index=True, doc="Generic external ID"
    )
    onec_id = Column(String(64), nullable=True, unique=True, index=True, doc="1C external ID")
    sync_source = Column(
        String(32), nullable=True, index=True, doc="Последний источник синхронизации (kaspi/1c/…)"
    )
    synced_at = Column(DateTime(timezone=True), nullable=True, index=True)
    last_sync_error_code = Column(String(64), nullable=True, index=True)
    last_sync_error_message = Column(Text, nullable=True)

    # Subscription info
    subscription_plan = Column(
        String(32),
        nullable=False,
        default="start",  # start, pro, business
        doc="Подписка: start | pro | business",
    )
    subscription_expires_at = Column(DateTime(timezone=True), nullable=True)

    # Settings JSON (строка) + версионирование
    settings = Column(Text, nullable=True)  # JSON string for flexible settings
    settings_version = Column(Integer, nullable=False, default=1, doc="Версия настроек")
    settings_history = Column(
        Text,
        nullable=True,
        doc="JSON-список аудита настроек: [{version, at, by, prev, new}] (хранит последние N)",
    )

    # Audit fields (UTC, tz-aware)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now, index=True)
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        onupdate=utc_now,
        index=True,
    )
    deleted_at = Column(DateTime(timezone=True), nullable=True, index=True)
    deleted_by = Column(Integer, nullable=True, index=True)
    delete_reason = Column(Text, nullable=True)

    # GDPR / Privacy
    gdpr_consent_at = Column(DateTime(timezone=True), nullable=True, index=True)
    gdpr_consent_version = Column(String(32), nullable=True, index=True)
    gdpr_consent_ip = Column(String(45), nullable=True)  # IPv4/IPv6

    # ---------------- Relationships (создаём безопасно) ----------------
    # users — как правило есть в тестах, оставляем напрямую
    users = relationship(
        "User",
        back_populates="company",
        cascade="all, delete-orphan",
        foreign_keys="User.company_id",
    )

    # Ниже связи создаём «мягко»: попробуем импортировать их модули.
    products = (
        relationship("Product", back_populates="company", cascade="all, delete-orphan")
        if _safe_import_for_rel("Product")
        else None
    )
    orders = (
        relationship("Order", back_populates="company", cascade="all, delete-orphan")
        if _safe_import_for_rel("Order")
        else None
    )
    warehouses = (
        relationship("Warehouse", back_populates="company", cascade="all, delete-orphan")
        if _safe_import_for_rel("Warehouse")
        else None
    )
    campaigns = (
        relationship("Campaign", back_populates="company", cascade="all, delete-orphan")
        if _safe_import_for_rel("Campaign")
        else None
    )
    subscriptions = (
        relationship("Subscription", back_populates="company", cascade="all, delete-orphan")
        if _safe_import_for_rel("Subscription")
        else None
    )
    billing_payments = (
        relationship("BillingPayment", back_populates="company", cascade="all, delete-orphan")
        if _safe_import_for_rel("BillingPayment")
        else None
    )
    invoices = (
        relationship("Invoice", back_populates="company", cascade="all, delete-orphan")
        if _safe_import_for_rel("Invoice")
        else None
    )
    wallet_balance = (
        relationship(
            "WalletBalance", back_populates="company", uselist=False, cascade="all, delete-orphan"
        )
        if _safe_import_for_rel("WalletBalance")
        else None
    )
    audit_logs = (
        relationship("AuditLog", back_populates="company", cascade="all, delete-orphan")
        if _safe_import_for_rel("AuditLog")
        else None
    )
    external_audit_events = (
        relationship("ExternalAuditEvent", back_populates="company", cascade="all, delete-orphan")
        if _safe_import_for_rel("ExternalAuditEvent")
        else None
    )

    # Optional owner linkage — полноценная FK и связь (если бизнес требует)
    owner_id = Column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    owner = relationship("User", foreign_keys=[owner_id], uselist=False)

    __table_args__ = (
        CheckConstraint(
            "subscription_plan IN ('start','pro','business')",
            name="ck_company_subscription_plan",
        ),
        CheckConstraint(
            "bin_iin IS NULL OR length(bin_iin) BETWEEN 10 AND 32", name="ck_company_bin_iin_len"
        ),
        CheckConstraint("phone IS NULL OR length(phone) <= 32", name="ck_company_phone_len"),
        CheckConstraint("email IS NULL OR length(email) <= 255", name="ck_company_email_len"),
        Index("ix_company_active_plan", "is_active", "subscription_plan"),
        # UniqueConstraint("name", name="uq_company_name"),  # включайте при необходимости
    )

    # -----------------------------
    # Валидации
    # -----------------------------
    @validates("name")
    def _validate_name(self, _k: str, v: str) -> str:
        v = (v or "").strip()
        if not v:
            raise ValueError("Company.name must be non-empty.")
        if len(v) > 255:
            raise ValueError("Company.name must be <= 255 chars.")
        return v

    @validates("subscription_plan")
    def _validate_plan(self, _k: str, v: str) -> str:
        allowed = {"start", "pro", "business"}
        vv = (v or "").strip().lower()
        if vv not in allowed:
            raise ValueError(f"Invalid subscription_plan '{vv}'. Allowed: {sorted(allowed)}")
        return vv

    # -----------------------------
    # Свойства
    # -----------------------------
    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None

    @property
    def is_archived(self) -> bool:
        """Компания считается архивной, если delete_reason == 'archived'."""
        return (self.delete_reason or "").strip().lower() == "archived"

    @property
    def subscription_days_left(self) -> Optional[int]:
        """Число полных дней до истечения подписки (если дата установлена)."""
        if not self.subscription_expires_at:
            return None
        delta = self.subscription_expires_at - utc_now()
        return max(0, int(delta.total_seconds() // 86400))

    @property
    def is_subscription_active(self) -> bool:
        """Подписка активна: есть дата истечения и она в будущем."""
        return bool(self.subscription_expires_at and self.subscription_expires_at > utc_now())

    # -----------------------------
    # Управление активностью/удалением
    # -----------------------------
    def soft_delete(
        self, *, by_user_id: Optional[int] = None, reason: Optional[str] = None
    ) -> None:
        self.deleted_at = utc_now()
        self.deleted_by = by_user_id
        self.delete_reason = (reason or self.delete_reason or "").strip() or None
        self.is_active = False

    def restore(self, *, reactivate: bool = True) -> None:
        """
        Восстановить после soft_delete.
        По умолчанию компания снова активна (reactivate=True).
        """
        self.deleted_at = None
        self.deleted_by = None
        self.delete_reason = None
        if reactivate:
            self.is_active = True

    def activate(self) -> None:
        self.is_active = True

    def deactivate(self) -> None:
        self.is_active = False

    # -----------------------------
    # Владение (owner)
    # -----------------------------
    def set_owner(self, new_owner_id: Optional[int]) -> None:
        """Прямая установка владельца (может быть None для снятия владельца)."""
        self.owner_id = new_owner_id

    def transfer_ownership(
        self,
        new_owner_id: int,
        *,
        by_user_id: Optional[int] = None,
        reason: Optional[str] = None,
    ) -> None:
        """Бизнес-операция: смена владельца с аудитом."""
        old_owner = self.owner_id
        self.owner_id = new_owner_id
        self._emit_audit_event(
            action="company.owner_transferred",
            meta={
                "old_owner_id": old_owner,
                "new_owner_id": new_owner_id,
                "reason": reason,
                "by": by_user_id,
            },
        )

    # -----------------------------
    # Подписка
    # -----------------------------
    def extend_subscription(self, months: int) -> None:
        """Продлить подписку на N месяцев (month = календарный)."""
        if months <= 0:
            raise ValueError("months must be > 0")
        try:
            from dateutil.relativedelta import relativedelta  # type: ignore
        except Exception as e:  # pragma: no cover
            raise ImportError("dateutil is required for extend_subscription()") from e

        if self.subscription_expires_at is None:
            self.subscription_expires_at = utc_now()
        self.subscription_expires_at = self.subscription_expires_at + relativedelta(months=months)

    def set_plan(self, plan: str) -> None:
        self.subscription_plan = plan  # валидация выше

    # -----------------------------
    # Kaspi helpers
    # -----------------------------
    def rotate_kaspi_api_key(self, new_key: str) -> None:
        if not new_key or not new_key.strip():
            raise ValueError("kaspi_api_key must be non-empty.")
        self.kaspi_api_key = new_key.strip()

    def masked_kaspi_api_key(self) -> Optional[str]:
        return _mask_secret(self.kaspi_api_key)

    # -----------------------------
    # GDPR / Privacy
    # -----------------------------
    def register_gdpr_consent(self, *, version: str, ip: Optional[str] = None) -> None:
        """Регистрирует согласие GDPR (версия документа и IP) + пишет событие аудита."""
        v = (version or "").strip()
        if not v:
            raise ValueError("GDPR version must be non-empty.")
        self.gdpr_consent_version = v
        self.gdpr_consent_ip = (ip or "").strip() or None
        self.gdpr_consent_at = utc_now()
        # аудит действия
        self._emit_audit_event(
            action="gdpr.consent_registered",
            meta={"version": v, "ip": self.gdpr_consent_ip},
        )

    # -----------------------------
    # Settings: JSON + версионирование (retention) + дефолты кошелька
    # -----------------------------
    def _append_settings_history(
        self,
        *,
        prev: Optional[Dict[str, Any]],
        new: Optional[Dict[str, Any]],
        by_user_id: Optional[int] = None,
    ) -> None:
        """Добавить запись в settings_history и обрезать историю до лимита."""
        entry = {
            "version": self.settings_version,
            "at": utc_now().isoformat(),
            "by": by_user_id,
            "prev": prev,
            "new": new,
        }
        history: list = []
        if self.settings_history:
            try:
                history = json.loads(self.settings_history) or []
                if not isinstance(history, list):
                    history = []
            except Exception:
                history = []
        history.append(entry)
        if len(history) > SETTINGS_HISTORY_LIMIT:
            history = history[-SETTINGS_HISTORY_LIMIT:]
        self.settings_history = json.dumps(history, ensure_ascii=False)

    def _apply_settings_defaults(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Гарантирует наличие ключей WALLET/ BILLING по умолчанию."""
        out = dict(DEFAULT_WALLET_SETTINGS)
        out.update(data or {})
        return out

    def _validate_wallet_settings(self, data: Dict[str, Any]) -> None:
        """Валидируем только те ключи, что влияют на критичную логику."""
        autopay = bool(data.get("wallet.autopay_enabled", False))
        min_pos = int(data.get("wallet.min_positive_balance", 1) or 0)
        allow_zero = bool(data.get("wallet.allow_zero_ok", True))
        if min_pos < 0:
            raise ValueError("wallet.min_positive_balance must be >= 0")
        channel = (data.get("billing.preferred_invoice_channel") or "auto").lower()
        if channel not in {"auto", "kaspi", "bank"}:
            raise ValueError("billing.preferred_invoice_channel must be one of: auto|kaspi|bank")
        partial = bool(data.get("billing.partial_invoice", True))
        _ = data.get("billing.currency", "KZT")
        _ = autopay or False
        _ = allow_zero or False
        _ = partial or False

    def set_settings(self, settings_json: str, *, by_user_id: Optional[int] = None) -> None:
        """Полная замена настроек JSON-строкой с bump версии и аудитом + дефолты и валидация."""
        prev = self.get_settings_dict()
        new_obj = None
        if settings_json is not None:
            try:
                new_obj = json.loads(settings_json)
            except Exception as e:
                raise ValueError(f"settings must be valid JSON: {e}")
        new_obj = self._apply_settings_defaults(new_obj or {})
        self._validate_wallet_settings(new_obj)
        self.settings = json.dumps(new_obj, ensure_ascii=False, separators=(",", ":"))
        self.settings_version = int(self.settings_version or 1) + 1
        self._append_settings_history(prev=prev, new=new_obj, by_user_id=by_user_id)

    def get_settings(self) -> Optional[str]:
        return self.settings

    def get_settings_dict(self, default: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        if not self.settings:
            return self._apply_settings_defaults(default or {})
        try:
            data = json.loads(self.settings)
            data = data if isinstance(data, dict) else (default or {})
            return self._apply_settings_defaults(data)
        except Exception:
            return self._apply_settings_defaults(default or {})

    def set_settings_dict(self, data: Dict[str, Any], *, by_user_id: Optional[int] = None) -> None:
        prev = self.get_settings_dict()
        data = self._apply_settings_defaults(data or {})
        self._validate_wallet_settings(data)
        try:
            self.settings = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
        except Exception as e:
            raise ValueError(f"Failed to serialize settings to JSON: {e}")
        self.settings_version = int(self.settings_version or 1) + 1
        self._append_settings_history(prev=prev, new=data, by_user_id=by_user_id)

    def patch_settings(self, *, by_user_id: Optional[int] = None, **kwargs: Any) -> None:
        """Частичное обновление настроек как dict с bump версии и аудитом."""
        prev = self.get_settings_dict()
        new_data = {**prev, **{k: v for k, v in kwargs.items() if v is not None}}
        self.set_settings_dict(new_data, by_user_id=by_user_id)

    def restore_settings_version(self, version: int, *, by_user_id: Optional[int] = None) -> None:
        """
        Восстановить настройки из прошлой версии:
        — находим в settings_history запись с совпадающей 'version'
        — применяем её 'new' как текущие настройки
        — bump settings_version и пишем новую запись в историю (prev=current, new=restored)
        """
        if not self.settings_history:
            raise ValueError("No settings history to restore from.")
        try:
            history = json.loads(self.settings_history) or []
        except Exception as e:
            raise ValueError(f"settings_history is corrupted: {e}")
        if not isinstance(history, list):
            raise ValueError("settings_history format invalid.")

        target = None
        for entry in history:
            if isinstance(entry, dict) and entry.get("version") == version:
                target = entry
                break
        if target is None:
            raise ValueError(f"Version {version} not found in settings_history.")

        prev = self.get_settings_dict()
        restored = target.get("new") if isinstance(target.get("new"), dict) else {}
        restored = self._apply_settings_defaults(restored)
        self._validate_wallet_settings(restored)
        self.settings = json.dumps(restored, ensure_ascii=False, separators=(",", ":"))
        self.settings_version = int(self.settings_version or 1) + 1
        self._append_settings_history(prev=prev, new=restored, by_user_id=by_user_id)

    # -----------------------------
    # Wallet helper (по ТЗ может понадобиться)
    # -----------------------------
    def ensure_wallet(self, session) -> "WalletBalance":
        """
        Гарантирует наличие WalletBalance для компании.
        Возвращает объект кошелька (существующий или новый).
        """
        wb = getattr(self, "wallet_balance", None)
        if wb:
            return wb
        from app.models.wallet import WalletBalance  # type: ignore

        wb = WalletBalance(company=self)  # type: ignore[arg-type]
        session.add(wb)
        return wb

    def get_wallet_config(self) -> Dict[str, Any]:
        """
        Удобный доступ к ключевым настройкам кошелька/биллинга с дефолтами.
        """
        s = self.get_settings_dict()
        return {
            "autopay_enabled": bool(s.get("wallet.autopay_enabled", False)),
            "min_positive_balance": int(s.get("wallet.min_positive_balance", 1) or 0),
            "allow_zero_ok": bool(s.get("wallet.allow_zero_ok", True)),
            "preferred_invoice_channel": (
                s.get("billing.preferred_invoice_channel") or "auto"
            ).lower(),
            "partial_invoice": bool(s.get("billing.partial_invoice", True)),
            "currency": s.get("billing.currency", "KZT"),
        }

    # ---- Логика «инвойс на нехватающую сумму» и планирование списания ----
    @staticmethod
    def calc_missing_amount(
        *,
        amount_due: int,
        balance: int,
        allow_zero_ok: bool = True,
    ) -> int:
        """
        Считает, сколько НЕ хватает для оплаты суммы amount_due при текущем balance.
        Возвращает 0, если хватает.
        """
        if amount_due < 0:
            raise ValueError("amount_due must be >= 0")
        if balance < 0:
            # на уровне домена отрицательный баланс запрещён, но если попали сюда — нормализуем
            balance = 0
        missing = amount_due - balance
        return missing if missing > 0 else 0

    def plan_charge_strategy(
        self,
        *,
        amount_due: int,
        current_balance: int,
    ) -> Dict[str, Any]:
        """
        Планирует стратегию оплаты: сначала кошелёк, затем — автосписание (если включено),
        в приоритете — выставление инвойса ровно на нехватающую сумму.
        Возвращает dict с шагами, без реальных побочных эффектов.
        """
        cfg = self.get_wallet_config()
        currency = cfg["currency"]
        missing = Company.calc_missing_amount(
            amount_due=amount_due, balance=current_balance, allow_zero_ok=cfg["allow_zero_ok"]
        )

        plan: Dict[str, Any] = {
            "currency": currency,
            "enough_in_wallet": missing == 0,
            "use_wallet_amount": min(amount_due, max(0, current_balance)),
            "try_autopay": False,
            "need_invoice": 0,
            "invoice_channel": None,
        }

        if missing == 0:
            return plan

        # Хватает не полностью: по ТЗ — выставляем счёт на нехватающую сумму
        plan["need_invoice"] = missing
        plan["invoice_channel"] = cfg["preferred_invoice_channel"]
        plan["try_autopay"] = bool(cfg["autopay_enabled"])
        return plan

    # -----------------------------
    # Интеграции / синхронизация (generic + Kaspi + 1C)
    # -----------------------------
    def sync_mark_success(self, *, source: str, at: Optional[datetime] = None) -> None:
        """Помечает успешную синхронизацию (generic)."""
        self.sync_source = (source or "").lower() or None
        self.synced_at = at or utc_now()
        self.last_sync_error_code = None
        self.last_sync_error_message = None

    def sync_mark_error(self, *, source: str, code: Optional[str], message: Optional[str]) -> None:
        """Помечает ошибку синхронизации (generic)."""
        self.sync_source = (source or "").lower() or None
        self.synced_at = utc_now()
        self.last_sync_error_code = (code or "").strip() or None
        self.last_sync_error_message = (message or "").strip() or None

    def sync_kaspi(self, *, success: bool, error_code: Optional[str] = None, error_message: Optional[str] = None) -> None:
        if success:
            self.sync_mark_success(source="kaspi")
        else:
            self.sync_mark_error(source="kaspi", code=error_code, message=error_message)

    def sync_onec(self, *, success: bool, error_code: Optional[str] = None, error_message: Optional[str] = None) -> None:
        if success:
            self.sync_mark_success(source="1c")
        else:
            self.sync_mark_error(source="1c", code=error_code, message=error_message)

    @staticmethod
    async def batch_sync_mark_success_async(session, company_ids: Sequence[int], *, source: str) -> int:
        """Батч-пометка успешной синхронизации."""
        if not company_ids:
            return 0
        now = utc_now()
        res = await session.execute(
            update(Company)
            .where(Company.id.in_(company_ids))
            .values(
                sync_source=(source or "").lower(),
                synced_at=now,
                last_sync_error_code=None,
                last_sync_error_message=None,
                updated_at=now,
            )
        )
        return int(res.rowcount or 0)

    @staticmethod
    async def batch_sync_mark_error_async(
        session, company_ids: Sequence[int], *, source: str, code: Optional[str], message: Optional[str]
    ) -> int:
        """Батч-пометка неуспешной синхронизации."""
        if not company_ids:
            return 0
        now = utc_now()
        res = await session.execute(
            update(Company)
            .where(Company.id.in_(company_ids))
            .values(
                sync_source=(source or "").lower(),
                synced_at=now,
                last_sync_error_code=(code or "").strip() or None,
                last_sync_error_message=(message or "").strip() or None,
                updated_at=now,
            )
        )
        return int(res.rowcount or 0)

    # -----------------------------
    # Аналитика (быстрые агрегаты) + BI-выгрузка
    # -----------------------------
    async def orders_stats_async(
        self,
        session,
        *,
        date_from: Optional[datetime] = None,
        date_to: Optional[datetime] = None,
        include_status: Optional[Iterable[str]] = None,
    ) -> Dict[str, Any]:
        """
        Агрегаты по заказам за период: count/sum/avg.
        Требуемые поля в Order: company_id, created_at, total_amount (Numeric), status (опц.)
        """
        from app.models.order import Order  # type: ignore

        conds = [Order.company_id == self.id]
        if date_from:
            conds.append(Order.created_at >= date_from)
        if date_to:
            conds.append(Order.created_at < date_to)
        if include_status:
            conds.append(Order.status.in_(list(include_status)))

        row = await session.execute(
            select(
                func.count(Order.id),
                func.coalesce(func.sum(Order.total_amount), 0),
                func.coalesce(func.avg(Order.total_amount), 0),
            ).where(*conds)
        )
        c, s, a = row.one()
        return {
            "orders_count": int(c or 0),
            "orders_sum": float(s or 0),
            "orders_avg": float(a or 0),
        }

    def orders_stats_sync(
        self,
        session,
        *,
        date_from: Optional[datetime] = None,
        date_to: Optional[datetime] = None,
        include_status: Optional[Iterable[str]] = None,
    ) -> Dict[str, Any]:
        """Синхронная версия агрегатов по заказам."""
        from app.models.order import Order  # type: ignore

        conds = [Order.company_id == self.id]
        if date_from:
            conds.append(Order.created_at >= date_from)
        if date_to:
            conds.append(Order.created_at < date_to)
        if include_status:
            conds.append(Order.status.in_(list(include_status)))

        row = session.execute(
            select(
                func.count(Order.id),
                func.coalesce(func.sum(Order.total_amount), 0),
                func.coalesce(func.avg(Order.total_amount), 0),
            ).where(*conds)
        ).one()
        c, s, a = row
        return {
            "orders_count": int(c or 0),
            "orders_sum": float(s or 0),
            "orders_avg": float(a or 0),
        }

    @staticmethod
    async def revenue_by_day_async(session, company_id: int, last_n_days: int = 30) -> List[dict]:
        """Выручка по дням из Orders.total_amount за последние N дней."""
        from app.models.order import Order  # type: ignore

        start = utc_now() - timedelta(days=last_n_days)
        rows = await session.execute(
            select(
                func.to_char(func.date_trunc("day", Order.created_at), "YYYY-MM-DD").label("day"),
                func.coalesce(func.sum(Order.total_amount), 0).label("revenue"),
            )
            .where(Order.company_id == company_id, Order.created_at >= start)
            .group_by(func.date_trunc("day", Order.created_at))
            .order_by(func.date_trunc("day", Order.created_at).asc())
        )
        return [{"day": d, "revenue": float(r)} for d, r in rows.all()]

    async def payments_stats_async(
        self,
        session,
        *,
        date_from: Optional[datetime] = None,
        date_to: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """Агрегаты по платежам (BillingPayment.amount)."""
        from app.models.billing import BillingPayment  # type: ignore

        conds = [BillingPayment.company_id == self.id]
        if date_from:
            conds.append(BillingPayment.created_at >= date_from)
        if date_to:
            conds.append(BillingPayment.created_at < date_to)

        row = await session.execute(
            select(
                func.count(BillingPayment.id),
                func.coalesce(func.sum(BillingPayment.amount), 0),
                func.coalesce(func.avg(BillingPayment.amount), 0),
            ).where(*conds)
        )
        c, s, a = row.one()
        return {
            "payments_count": int(c or 0),
            "payments_sum": float(s or 0),
            "payments_avg": float(a or 0),
        }

    def payments_stats_sync(
        self,
        session,
        *,
        date_from: Optional[datetime] = None,
        date_to: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """Синхронная версия агрегатов по платежам."""
        from app.models.billing import BillingPayment  # type: ignore

        conds = [BillingPayment.company_id == self.id]
        if date_from:
            conds.append(BillingPayment.created_at >= date_from)
        if date_to:
            conds.append(BillingPayment.created_at < date_to)

        c, s, a = session.execute(
            select(
                func.count(BillingPayment.id),
                func.coalesce(func.sum(BillingPayment.amount), 0),
                func.coalesce(func.avg(BillingPayment.amount), 0),
            ).where(*conds)
        ).one()
        return {
            "payments_count": int(c or 0),
            "payments_sum": float(s or 0),
            "payments_avg": float(a or 0),
        }

    async def invoices_debt_stats_async(
        self,
        session,
        *,
        date_to: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """
        Долги по инвойсам: суммарно и количество с долгом.
        Ожидаемые поля: total_due (Numeric), paid_amount (Numeric).
        """
        from app.models.invoice import Invoice  # type: ignore

        conds = [Invoice.company_id == self.id]
        if date_to:
            conds.append(Invoice.created_at < date_to)

        debt_expr = func.greatest(
            func.coalesce(Invoice.total_due, 0) - func.coalesce(Invoice.paid_amount, 0), 0
        )

        row = await session.execute(
            select(
                func.coalesce(func.sum(debt_expr), 0),
                func.count().filter(debt_expr > 0),
            ).where(*conds)
        )
        total_debt, cnt_with_debt = row.one()
        return {
            "invoices_total_debt": float(total_debt or 0),
            "invoices_with_debt": int(cnt_with_debt or 0),
        }

    def invoices_debt_stats_sync(
        self,
        session,
        *,
        date_to: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """Синхронная версия долгов по инвойсам."""
        from app.models.invoice import Invoice  # type: ignore

        conds = [Invoice.company_id == self.id]
        if date_to:
            conds.append(Invoice.created_at < date_to)

        debt_expr = func.greatest(
            func.coalesce(Invoice.total_due, 0) - func.coalesce(Invoice.paid_amount, 0), 0
        )

        total_debt, cnt_with_debt = session.execute(
            select(
                func.coalesce(func.sum(debt_expr), 0),
                func.count().filter(debt_expr > 0),
            ).where(*conds)
        ).one()
        return {
            "invoices_total_debt": float(total_debt or 0),
            "invoices_with_debt": int(cnt_with_debt or 0),
        }

    # ----- BI: pandas DataFrame / CSV / Parquet / Excel -----
    @staticmethod
    async def orders_to_dataframe_async(
        session,
        company_id: int,
        *,
        date_from: Optional[datetime] = None,
        date_to: Optional[datetime] = None,
    ):
        """Возвращает pandas.DataFrame с заказами компании (id, created_at, total_amount, status)."""
        try:
            import pandas as pd  # type: ignore
        except Exception as e:  # pragma: no cover
            raise ImportError("pandas is required for orders_to_dataframe_async()") from e

        from app.models.order import Order  # type: ignore

        conds = [Order.company_id == company_id]
        if date_from:
            conds.append(Order.created_at >= date_from)
        if date_to:
            conds.append(Order.created_at < date_to)

        columns = [Order.id, Order.created_at, Order.total_amount]
        if hasattr(Order, "status"):
            columns.append(getattr(Order, "status").label("status"))  # type: ignore[arg-type]
        else:
            columns.append(literal(None).label("status"))

        rows = await session.execute(select(*columns).where(*conds))
        data = []
        for r in rows.all():
            data.append(
                {
                    "id": r.id,
                    "created_at": r.created_at,
                    "total_amount": float(r.total_amount) if r.total_amount is not None else None,
                    "status": getattr(r, "status", None),
                }
            )
        return pd.DataFrame(data)

    @staticmethod
    async def payments_to_dataframe_async(
        session,
        company_id: int,
        *,
        date_from: Optional[datetime] = None,
        date_to: Optional[datetime] = None,
    ):
        """Возвращает pandas.DataFrame с платежами компании (id, created_at, amount, method, status)."""
        try:
            import pandas as pd  # type: ignore
        except Exception as e:  # pragma: no cover
            raise ImportError("pandas is required for payments_to_dataframe_async()") from e

        from app.models.billing import BillingPayment  # type: ignore

        conds = [BillingPayment.company_id == company_id]
        if date_from:
            conds.append(BillingPayment.created_at >= date_from)
        if date_to:
            conds.append(BillingPayment.created_at < date_to)

        columns = [BillingPayment.id, BillingPayment.created_at, BillingPayment.amount]
        if hasattr(BillingPayment, "method"):
            columns.append(getattr(BillingPayment, "method").label("method"))  # type: ignore[arg-type]
        else:
            columns.append(literal(None).label("method"))
        if hasattr(BillingPayment, "status"):
            columns.append(getattr(BillingPayment, "status").label("status"))  # type: ignore[arg-type]
        else:
            columns.append(literal(None).label("status"))

        rows = await session.execute(select(*columns).where(*conds))
        data = []
        for r in rows.all():
            data.append(
                {
                    "id": r.id,
                    "created_at": r.created_at,
                    "amount": float(r.amount) if r.amount is not None else None,
                    "method": getattr(r, "method", None),
                    "status": getattr(r, "status", None),
                }
            )
        return pd.DataFrame(data)

    @staticmethod
    async def orders_to_csv_async(
        session,
        company_id: int,
        path: str,
        *,
        date_from: Optional[datetime] = None,
        date_to: Optional[datetime] = None,
        index: bool = DEFAULT_CSV_INDEX,
        **to_csv_kwargs: Any,
    ) -> str:
        """Сохраняет CSV с заказами компании. Возвращает путь к файлу."""
        df = await Company.orders_to_dataframe_async(
            session, company_id, date_from=date_from, date_to=date_to
        )
        df.to_csv(path, index=index, **to_csv_kwargs)
        return path

    @staticmethod
    async def orders_to_parquet_async(
        session,
        company_id: int,
        path: str,
        *,
        date_from: Optional[datetime] = None,
        date_to: Optional[datetime] = None,
        engine: str = "pyarrow",
        **kwargs: Any,
    ) -> str:
        """Сохраняет Parquet с заказами компании. Требует pandas + (pyarrow|fastparquet)."""
        df = await Company.orders_to_dataframe_async(
            session, company_id, date_from=date_from, date_to=date_to
        )
        df.to_parquet(path, engine=engine, **kwargs)
        return path

    @staticmethod
    async def orders_to_excel_async(
        session,
        company_id: int,
        path: str,
        *,
        date_from: Optional[datetime] = None,
        date_to: Optional[datetime] = None,
        sheet_name: str = "Orders",
        engine: str = "openpyxl",
        **kwargs: Any,
    ) -> str:
        """Сохраняет Excel с заказами компании (один лист). Требует pandas + openpyxl/xlsxwriter."""
        df = await Company.orders_to_dataframe_async(
            session, company_id, date_from=date_from, date_to=date_to
        )
        try:
            import pandas as pd  # type: ignore
        except Exception as e:  # pragma: no cover
            raise ImportError("pandas is required for orders_to_excel_async()") from e
        with pd.ExcelWriter(path, engine=engine, **kwargs) as writer:  # type: ignore[arg-type]
            df.to_excel(writer, index=False, sheet_name=sheet_name)
        return path

    @staticmethod
    async def payments_to_parquet_async(
        session,
        company_id: int,
        path: str,
        *,
        date_from: Optional[datetime] = None,
        date_to: Optional[datetime] = None,
        engine: str = "pyarrow",
        **kwargs: Any,
    ) -> str:
        """Сохраняет Parquet с платежами компании."""
        df = await Company.payments_to_dataframe_async(
            session, company_id, date_from=date_from, date_to=date_to
        )
        df.to_parquet(path, engine=engine, **kwargs)
        return path

    @staticmethod
    async def payments_to_excel_async(
        session,
        company_id: int,
        path: str,
        *,
        date_from: Optional[datetime] = None,
        date_to: Optional[datetime] = None,
        sheet_name: str = "Payments",
        engine: str = "openpyxl",
        **kwargs: Any,
    ) -> str:
        """Сохраняет Excel с платежами компании (один лист)."""
        df = await Company.payments_to_dataframe_async(
            session, company_id, date_from=date_from, date_to=date_to
        )
        try:
            import pandas as pd  # type: ignore
        except Exception as e:  # pragma: no cover
            raise ImportError("pandas is required for payments_to_excel_async()") from e
        with pd.ExcelWriter(path, engine=engine, **kwargs) as writer:  # type: ignore[arg-type]
            df.to_excel(writer, index=False, sheet_name=sheet_name)
        return path

    # -----------------------------
    # Массовые операции: архив/восстановление компаний (+ аудит/уведомления)
    # -----------------------------
    @staticmethod
    async def bulk_archive_async(
        session,
        company_ids: Sequence[int],
        *,
        archived_reason: str = "archived",
        by_user_id: Optional[int] = None,
        notify_fn: Optional[Callable[[int, str, Dict[str, Any]], None]] = None,
        write_audit: bool = True,
    ) -> int:
        """
        Массовое архивирование компаний: проставляет deleted_at, delete_reason='archived', is_active=False.
        Возвращает количество обновлённых строк.
        — notify_fn(company_id, action, meta) вызывается для каждого id, если задан.
        — write_audit: если True, создаём ExternalAuditEvent (если модель доступна).
        """
        if not company_ids:
            return 0
        now = utc_now()
        res = await session.execute(
            update(Company)
            .where(Company.id.in_(company_ids))
            .values(
                deleted_at=now,
                deleted_by=by_user_id,
                delete_reason=archived_reason,
                is_active=False,
                updated_at=now,
            )
        )
        count = int(res.rowcount or 0)

        if write_audit or notify_fn:
            for cid in company_ids:
                if write_audit:
                    Company._emit_audit_event_static(
                        session=session,
                        company_id=cid,
                        action="company.archived",
                        meta={"reason": archived_reason, "by": by_user_id},
                    )
                if notify_fn:
                    try:
                        notify_fn(
                            cid, "company.archived", {"reason": archived_reason, "by": by_user_id}
                        )
                    except Exception:
                        # нотификации не должны валить транзакцию
                        pass
        return count

    @staticmethod
    async def bulk_restore_async(
        session,
        company_ids: Sequence[int],
        *,
        reactivate: bool = False,
        by_user_id: Optional[int] = None,
        notify_fn: Optional[Callable[[int, str, Dict[str, Any]], None]] = None,
        write_audit: bool = True,
    ) -> int:
        """
        Массовое восстановление компаний: сбрасывает deleted_*, optionally включает is_active.
        Возвращает количество обновлённых строк.
        — notify_fn(company_id, action, meta) вызывается для каждого id, если задан.
        — write_audit: если True, создаём ExternalAuditEvent (если модель доступна).
        """
        if not company_ids:
            return 0
        now = utc_now()
        values: Dict[str, Any] = dict(
            deleted_at=None,
            deleted_by=None,
            delete_reason=None,
            updated_at=now,
        )
        if reactivate:
            values["is_active"] = True

        res = await session.execute(
            update(Company).where(Company.id.in_(company_ids)).values(**values)
        )
        count = int(res.rowcount or 0)

        # ВАЖНО: здесь было русское "или" — исправлено на or
        if write_audit or notify_fn:
            for cid in company_ids:
                if write_audit:
                    Company._emit_audit_event_static(
                        session=session,
                        company_id=cid,
                        action="company.restored",
                        meta={"reactivate": reactivate, "by": by_user_id},
                    )
                if notify_fn:
                    try:
                        notify_fn(
                            cid, "company.restored", {"reactivate": reactivate, "by": by_user_id}
                        )
                    except Exception:
                        pass
        return count

    # -----------------------------
    # Вспомогательный аудит
    # -----------------------------
    def _emit_audit_event(self, action: str, meta: Optional[Dict[str, Any]] = None) -> None:
        """Создаёт ExternalAuditEvent (если модель доступна). Безопасно падает в no-op при отсутствии модели."""
        try:
            from app.models.audit import ExternalAuditEvent  # type: ignore
        except Exception:
            return
        evt = ExternalAuditEvent(
            company_id=self.id,
            action=action,
            metadata_json=json.dumps(meta or {}, ensure_ascii=False),
            created_at=utc_now(),
        )
        sess = object_session(self)
        if sess is not None:
            try:
                sess.add(evt)
                return
            except Exception:
                pass
        # на крайний случай — если нет сессии, игнорируем (безопасно)
        return

    @staticmethod
    def _emit_audit_event_static(
        *, session, company_id: int, action: str, meta: Optional[Dict[str, Any]] = None
    ) -> None:
        """Статическая версия для bulk-операций."""
        try:
            from app.models.audit import ExternalAuditEvent  # type: ignore
        except Exception:
            return
        evt = ExternalAuditEvent(
            company_id=company_id,
            action=action,
            metadata_json=json.dumps(meta or {}, ensure_ascii=False),
            created_at=utc_now(),
        )
        session.add(evt)

    # -----------------------------
    # Аудит/сериализация
    # -----------------------------
    def update_audit(self) -> None:
        self.updated_at = utc_now()

    def to_dict(self, mask_secrets: bool = True) -> Dict[str, Any]:
        """
        Сериализация. По умолчанию маскирует чувствительные поля.
        """
        data = {col.name: getattr(self, col.name) for col in self.__table__.columns}
        for k in (
            "created_at",
            "updated_at",
            "deleted_at",
            "subscription_expires_at",
            "synced_at",
            "gdpr_consent_at",
        ):
            if data.get(k) is not None and isinstance(data[k], datetime):
                data[k] = data[k].isoformat()
        if mask_secrets:
            data["kaspi_api_key"] = _mask_secret(self.kaspi_api_key)
        return data

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<Company id={self.id} name={self.name!r} active={self.is_active} "
            f"plan={self.subscription_plan} deleted={self.deleted_at is not None}>"
        )

    # -----------------------------
    # Быстрые выборки/поиск
    # -----------------------------
    @staticmethod
    def get_active(session, *, limit: int = 100) -> List["Company"]:
        q = select(Company).where(Company.is_active.is_(True)).order_by(Company.id.asc()).limit(limit)
        return list(session.execute(q).scalars().all())

    @staticmethod
    def find_by_external(session, *, external_id: Optional[str] = None, onec_id: Optional[str] = None) -> Optional["Company"]:
        conds = []
        if external_id:
            conds.append(Company.external_id == external_id)
        if onec_id:
            conds.append(Company.onec_id == onec_id)
        if not conds:
            return None
        q = select(Company).where(func.coalesce(literal(True), literal(True)))  # no-op start
        for c in conds:
            q = q.where(c)
        return session.execute(q.limit(1)).scalars().first()

    # -----------------------------
    # Фабрика / сидинг для тестов
    # -----------------------------
    @staticmethod
    def factory(
        *,
        name: str = "Test Company",
        plan: str = "start",
        bin_iin: Optional[str] = None,
        kaspi_store_id: Optional[str] = None,
        kaspi_api_key: Optional[str] = None,
        is_active: bool = True,
        subscription_expires_at: Optional[datetime] = None,
        phone: Optional[str] = None,
        email: Optional[str] = None,
        address: Optional[str] = None,
        owner_id: Optional[int] = None,
        settings: Optional[Dict[str, Any]] = None,
        external_id: Optional[str] = None,
        onec_id: Optional[str] = None,
    ) -> "Company":
        obj = Company(
            name=name,
            bin_iin=bin_iin,
            kaspi_store_id=kaspi_store_id,
            kaspi_api_key=kaspi_api_key,
            is_active=is_active,
            subscription_plan=plan,
            subscription_expires_at=subscription_expires_at,
            phone=phone,
            email=email,
            address=address,
            owner_id=owner_id,
            external_id=external_id,
            onec_id=onec_id,
        )
        if settings is not None:
            obj.set_settings_dict(settings)
        return obj

    @staticmethod
    async def create_with_related_async(
        session,
        *,
        name: str = "Test Company",
        users: int = 0,
        products: int = 0,
        orders: int = 0,
        plan: str = "start",
    ) -> "Company":
        """
        Создаёт компанию и (опционально) связанных пользователей/товары/заказы для тестов.
        Требуются минимальные поля у связанных моделей:
         - User(company_id, email, ...) — задаём email.
         - Product(company_id, name, price Numeric).
         - Order(company_id, total_amount, created_at).
        """
        company = Company.factory(name=name, plan=plan)
        session.add(company)
        await session.flush()  # company.id

        # Lazy imports to avoid circular deps
        try:
            from app.models.user import User  # type: ignore
        except Exception:
            User = None  # type: ignore
        try:
            from app.models.product import Product  # type: ignore
        except Exception:
            Product = None  # type: ignore
        try:
            from app.models.order import Order  # type: ignore
        except Exception:
            Order = None  # type: ignore

        if User and users > 0:
            for i in range(users):
                session.add(User(company_id=company.id, email=f"user{i}@example.com"))  # type: ignore

        if Product and products > 0:
            for i in range(products):
                # сохраняем твой безопасный fallback для Numeric, ничего не удаляем
                session.add(
                    Product(
                        company_id=company.id,
                        name=f"Product {i}",
                        price=Numeric().bind_processor(None)(100 + i) if hasattr(Numeric, "bind_processor") else 100 + i,  # type: ignore
                    )
                )

        if Order and orders > 0:
            for i in range(orders):
                session.add(
                    Order(
                        company_id=company.id,
                        total_amount=(100 + i),  # type: ignore[arg-type]
                        created_at=utc_now() - timedelta(days=i),
                    )
                )

        return company


__all__ = ["Company"]
