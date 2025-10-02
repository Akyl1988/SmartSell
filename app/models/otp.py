# app/models/otp.py
from __future__ import annotations

import enum
import hmac
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, Optional, Sequence, Tuple, Union, Type

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    and_,
    delete,
    func,
    or_,
    select,
    update,
    UniqueConstraint,
    event,
)
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import relationship, validates
from sqlalchemy.orm.exc import UnmappedClassError
from sqlalchemy.orm import class_mapper

from app.models.base import Base


# ---------------------------------------------------------------------------
# Общие утилиты / SIEM
# ---------------------------------------------------------------------------

def utc_now() -> datetime:
    """Naive UTC 'сейчас' (без tzinfo) — удобно для SQLite и кросс-БД по проекту."""
    return datetime.utcnow()


def constant_time_equals(a: str | bytes, b: str | bytes) -> bool:
    """Сравнение в константное время (защита от тайминговых атак)."""
    if isinstance(a, str):
        a = a.encode("utf-8")
    if isinstance(b, str):
        b = b.encode("utf-8")
    return hmac.compare_digest(a, b)


_AUDIT_SINK: Optional[Callable[[str, Dict[str, Any]], None]] = None


def configure_audit_sink(sink: Optional[Callable[[str, Dict[str, Any]], None]]) -> None:
    """Регистрирует внешнюю приёмную точку для аудита (например, логер/шина)."""
    global _AUDIT_SINK
    _AUDIT_SINK = sink


def get_audit_sink() -> Optional[Callable[[str, Dict[str, Any]], None]]:
    return _AUDIT_SINK


def _emit(event_name: str, payload: Dict[str, Any]) -> None:
    """Безопасно публикует событие в подключённый sink (если он есть)."""
    sink = _AUDIT_SINK
    if sink:
        try:
            sink(event_name, payload)
        except Exception:
            # Никогда не ломаем бизнес-логику из-за проблем с аудитом
            pass


# ---------------------------------------------------------------------------
# SINGLE-MAPPER GUARD FOR OTPCode
# ---------------------------------------------------------------------------

def _get_existing_mapped_class(name: str):
    """
    Вернёт уже смэпленный класс с данным именем из Declarative Registry, если он есть
    и реально промаплен. Нужен, чтобы не задвоить OTPCode, если он объявлен в другом модуле.
    """
    try:
        reg = Base.registry._class_registry  # type: ignore[attr-defined]
    except Exception:
        return None
    value = reg.get(name)
    if isinstance(value, type):
        try:
            class_mapper(value)
            return value
        except UnmappedClassError:
            return None
        except Exception:
            return None
    return None


def _patch_otpcode_api(cls: Type[Any]) -> None:
    """
    Аккуратно добавляем недостающие методы в уже существующий OTPCode (если он был смэплен
    вне этого файла), не затирая чужую бизнес-логику.
    """
    if not hasattr(cls, "is_expired"):
        def is_expired(self) -> bool:
            return utc_now() >= (getattr(self, "expires_at", None) or utc_now())
        setattr(cls, "is_expired", is_expired)

    if not hasattr(cls, "can_be_verified"):
        def can_be_verified(self) -> bool:
            return not bool(getattr(self, "is_used", False)) and not self.is_expired()
        setattr(cls, "can_be_verified", can_be_verified)

    if not hasattr(cls, "mark_used"):
        def mark_used(self) -> None:
            self.is_used = True
            self.verified_at = utc_now()
            self.updated_at = utc_now()
        setattr(cls, "mark_used", mark_used)

    if not hasattr(cls, "verify_plain"):
        def verify_plain(self, plain_code: str) -> bool:
            if not self.can_be_verified():
                return False
            ok = constant_time_equals((plain_code or ""), (getattr(self, "code", "") or ""))
            if ok:
                self.mark_used()
            else:
                self.attempts = int(getattr(self, "attempts", 0) or 0) + 1
                self.updated_at = utc_now()
            return ok
        setattr(cls, "verify_plain", verify_plain)

    if not hasattr(cls, "factory"):
        @staticmethod
        def factory(*, phone: str = "77000000000", code: str = "123456",
                    purpose: str = "login", ttl_minutes: int = 5,
                    user_id: Optional[int] = None):
            now = utc_now()
            return cls(
                phone=phone,
                code=code,
                purpose=(purpose or "login"),
                expires_at=now + timedelta(minutes=max(ttl_minutes, 1)),
                is_used=False,
                attempts=0,
                user_id=user_id,
                created_at=now,
                updated_at=now,
            )
        setattr(cls, "factory", factory)

    if not hasattr(cls, "a_find_latest_active"):
        @staticmethod
        async def a_find_latest_active(session: AsyncSession, *, phone: str, purpose: str = "login"):
            rows = await session.execute(
                select(cls)
                .where(
                    cls.phone == phone,
                    cls.purpose == purpose,
                    cls.is_used.is_(False),
                    cls.expires_at > utc_now(),
                )
                .order_by(cls.created_at.desc())
                .limit(1)
            )
            return rows.scalar_one_or_none()
        setattr(cls, "a_find_latest_active", a_find_latest_active)

    if not hasattr(cls, "a_verify_and_mark"):
        @staticmethod
        async def a_verify_and_mark(session: AsyncSession, *, phone: str, purpose: str, code: str, max_attempts: int = 5) -> bool:
            rec = await cls.a_find_latest_active(session, phone=phone, purpose=purpose)
            if not rec:
                return False
            if int(getattr(rec, "attempts", 0) or 0) >= max_attempts:
                return False
            if rec.verify_plain(code):
                await session.flush()
                _emit("otp.legacy.verify.ok", {"id": rec.id, "phone": rec.phone, "purpose": rec.purpose})
                return True
            await session.flush()
            _emit("otp.legacy.verify.fail", {"id": rec.id, "phone": rec.phone, "purpose": rec.purpose})
            return False
        setattr(cls, "a_verify_and_mark", a_verify_and_mark)

    if not hasattr(cls, "a_cleanup_expired"):
        @staticmethod
        async def a_cleanup_expired(session: AsyncSession, *, older_than_minutes: int = 60) -> int:
            threshold = utc_now() - timedelta(minutes=older_than_minutes)
            res = await session.execute(
                delete(cls).where(
                    or_(
                        cls.expires_at < threshold,
                        and_(
                            cls.is_used.is_(True),
                            cls.verified_at != None,  # noqa: E711
                            cls.verified_at < threshold,
                        ),
                    )
                )
            )
            return int(res.rowcount or 0)
        setattr(cls, "a_cleanup_expired", a_cleanup_expired)


_existing_otpcode = _get_existing_mapped_class("OTPCode")


# ---------------------------------------------------------------------------
# Legacy модель — объявляем ТОЛЬКО если ранее не было смэплено
# ---------------------------------------------------------------------------

if _existing_otpcode is None:

    class OTPCode(Base):
        """
        Легаси-модель для совместимости (таблица otp_codes).
        Минимальные поля и логика валидации/верификации.
        """
        __tablename__ = "otp_codes"
        __mapper_args__ = {"eager_defaults": True}
        __table_args__ = (
            Index("ix_otpcode_phone", "phone"),
            Index("ix_otpcode_purpose", "purpose"),
            Index("ix_otpcode_created_at", "created_at"),
            Index("ix_otpcode_expires_at", "expires_at"),
            Index("ix_otpcode_is_used", "is_used"),
            Index("ix_otpcode_phone_purpose_created", "phone", "purpose", "created_at"),
            CheckConstraint("attempts >= 0", name="ck_otpcode_attempts_non_negative"),
            {"extend_existing": True},
        )

        id = Column(Integer, primary_key=True, autoincrement=True, index=True)
        phone = Column(String(32), nullable=False)
        code = Column(String(16), nullable=False)
        purpose = Column(String(32), nullable=False, default="login")

        # дефолт: +5 минут с момента создания
        expires_at = Column(DateTime, nullable=False, default=lambda: utc_now() + timedelta(minutes=5))
        is_used = Column(Boolean, nullable=False, default=False)
        attempts = Column(Integer, nullable=False, default=0)

        user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
        user = relationship("User", backref="otp_codes", foreign_keys=[user_id], lazy="selectin")

        created_at = Column(DateTime, nullable=False, default=utc_now)
        verified_at = Column(DateTime, nullable=True)
        updated_at = Column(DateTime, nullable=False, default=utc_now, onupdate=utc_now)

        # ---- Валидации ----
        @validates("phone")
        def _v_phone(self, _k: str, v: Optional[str]) -> str:
            v = (v or "").strip()
            if not v:
                raise ValueError("phone is required")
            if len(v) > 32:
                raise ValueError("phone must be <= 32 chars")
            return v

        @validates("code")
        def _v_code(self, _k: str, v: Optional[str]) -> str:
            v = (v or "").strip()
            if not v:
                raise ValueError("code is required")
            if len(v) > 16:
                raise ValueError("code must be <= 16 chars")
            return v

        @validates("purpose")
        def _v_purpose(self, _k: str, v: Optional[str]) -> str:
            return (v or "login").strip().lower()

        @validates("expires_at")
        def _v_expires(self, _k: str, v: Optional[datetime]) -> datetime:
            return v or (utc_now() + timedelta(minutes=5))

        # ---- Бизнес-логика ----
        def is_expired(self) -> bool:
            return utc_now() >= (self.expires_at or utc_now())

        def can_be_verified(self) -> bool:
            return not self.is_used and not self.is_expired()

        def mark_used(self) -> None:
            self.is_used = True
            self.verified_at = utc_now()
            self.updated_at = utc_now()

        def verify_plain(self, plain_code: str) -> bool:
            if not self.can_be_verified():
                return False
            ok = constant_time_equals((plain_code or ""), (self.code or ""))
            if ok:
                self.mark_used()
            else:
                self.attempts = int(self.attempts or 0) + 1
                self.updated_at = utc_now()
            return ok

        # ---- Фабрики/асинхронные хелперы ----
        @staticmethod
        def factory(
            *,
            phone: str = "77000000000",
            code: str = "123456",
            purpose: str = "login",
            ttl_minutes: int = 5,
            user_id: Optional[int] = None,
        ) -> "OTPCode":
            now = utc_now()
            return OTPCode(
                phone=phone,
                code=code,
                purpose=(purpose or "login"),
                expires_at=now + timedelta(minutes=max(ttl_minutes, 1)),
                is_used=False,
                attempts=0,
                user_id=user_id,
                created_at=now,
                updated_at=now,
            )

        @staticmethod
        async def a_find_latest_active(
            session: AsyncSession, *, phone: str, purpose: str = "login"
        ) -> Optional["OTPCode"]:
            rows = await session.execute(
                select(OTPCode)
                .where(
                    OTPCode.phone == phone,
                    OTPCode.purpose == purpose,
                    OTPCode.is_used.is_(False),
                    OTPCode.expires_at > utc_now(),
                )
                .order_by(OTPCode.created_at.desc())
                .limit(1)
            )
            return rows.scalar_one_or_none()

        @staticmethod
        async def a_verify_and_mark(
            session: AsyncSession, *, phone: str, purpose: str, code: str, max_attempts: int = 5
        ) -> bool:
            rec = await OTPCode.a_find_latest_active(session, phone=phone, purpose=purpose)
            if not rec:
                return False
            if rec.attempts >= max_attempts:
                return False
            if rec.verify_plain(code):
                await session.flush()
                _emit("otp.legacy.verify.ok", {"id": rec.id, "phone": rec.phone, "purpose": rec.purpose})
                return True
            await session.flush()
            _emit("otp.legacy.verify.fail", {"id": rec.id, "phone": rec.phone, "purpose": rec.purpose})
            return False

        @staticmethod
        async def a_cleanup_expired(session: AsyncSession, *, older_than_minutes: int = 60) -> int:
            threshold = utc_now() - timedelta(minutes=older_than_minutes)
            res = await session.execute(
                delete(OTPCode).where(
                    or_(
                        OTPCode.expires_at < threshold,
                        and_(
                            OTPCode.is_used.is_(True),
                            OTPCode.verified_at != None,  # noqa: E711
                            OTPCode.verified_at < threshold,
                        ),
                    )
                )
            )
            return int(res.rowcount or 0)

        def __repr__(self) -> str:  # pragma: no cover
            return (
                f"<OTPCode id={self.id} phone='{self.phone}' purpose='{self.purpose}' "
                f"used={self.is_used} exp={self.expires_at}>"
            )

else:
    # Класс уже существует — используем его и подмешиваем недостающие методы.
    OTPCode = _existing_otpcode  # type: ignore[assignment]
    _patch_otpcode_api(OTPCode)  # type: ignore[arg-type]


# --- ЕДИНАЯ ТОЧКА РЕГИСТРАЦИИ LISTENERS ДЛЯ OTPCode (для обоих сценариев) ---

def _otp_init_fn(target, args, kwargs) -> None:
    """
    Подстраховка на уровне инициализации инстанса:
    если expires_at / attempts / is_used не заданы, проставим дефолты.
    """
    if getattr(target, "expires_at", None) is None:
        target.expires_at = utc_now() + timedelta(minutes=5)
    if getattr(target, "attempts", None) is None:
        target.attempts = 0
    if getattr(target, "is_used", None) is None:
        target.is_used = False


def _otp_before_insert_fn(_mapper, _connection, target) -> None:
    """
    Гарантируем заполненность обязательных полей, даже если объект создан
    с None в expires_at/и т.п. (важно для SQLite и простых тестов).
    """
    if not getattr(target, "expires_at", None):
        target.expires_at = utc_now() + timedelta(minutes=5)
    if getattr(target, "attempts", None) is None:
        target.attempts = 0
    if getattr(target, "is_used", None) is None:
        target.is_used = False
    if not getattr(target, "created_at", None):
        target.created_at = utc_now()
    if not getattr(target, "updated_at", None):
        target.updated_at = utc_now()


def _otp_before_update_fn(_mapper, _connection, target) -> None:
    target.updated_at = utc_now()
    if getattr(target, "attempts", None) is None:
        target.attempts = 0
    if getattr(target, "is_used", None) is None:
        # фикс: раньше могла быть опечатка target.is_used = False()
        target.is_used = False


def _ensure_otp_listeners(model_cls: Type[Any]) -> None:
    """
    Регистрируем слушатели, если ещё не зарегистрированы.
    Это устраняет проблему, когда OTPCode уже смэплен в другом месте, и
    наши декораторы не сработали, а также защищает от повторной регистрации.
    """
    # init
    if not event.contains(model_cls, "init", _otp_init_fn):
        event.listen(model_cls, "init", _otp_init_fn, propagate=True)
    # before_insert
    if not event.contains(model_cls, "before_insert", _otp_before_insert_fn):
        event.listen(model_cls, "before_insert", _otp_before_insert_fn, propagate=True)
    # before_update
    if not event.contains(model_cls, "before_update", _otp_before_update_fn):
        event.listen(model_cls, "before_update", _otp_before_update_fn, propagate=True)


# Всегда гарантируем подключение слушателей для выбранного класса OTPCode
_ensure_otp_listeners(OTPCode)


# ---------------------------------------------------------------------------
# Новая модель — enterprise-ready: otp_attempts (без изменений логики)
# ---------------------------------------------------------------------------

class OtpPurpose(str, enum.Enum):
    LOGIN = "login"
    REGISTER = "register"
    RESET_PASSWORD = "reset_password"
    TWO_FACTOR = "two_factor"
    CHANGE_PHONE = "change_phone"
    GENERIC = "generic"


class OtpChannel(str, enum.Enum):
    SMS = "sms"
    WHATSAPP = "whatsapp"
    VIBER = "viber"
    VOICE = "voice"
    EMAIL = "email"
    PUSH = "push"


DEFAULT_OTP_TTL_MIN = 5


class OtpAttempt(Base):
    """
    Enterprise-вариант хранения OTP-попыток с расширенной аналитикой/ограничениями.
    """
    __tablename__ = "otp_attempts"
    __mapper_args__ = {"eager_defaults": True}
    __table_args__ = (
        Index("ix_otp_attempt_phone", "phone"),
        Index("ix_otp_attempt_purpose", "purpose"),
        Index("ix_otp_attempt_channel", "channel"),
        Index("ix_otp_attempt_created_at", "created_at"),
        Index("ix_otp_attempt_expires_at", "expires_at"),
        Index("ix_otp_attempt_verified", "is_verified"),
        Index("ix_otp_attempt_blocked", "is_blocked"),
        UniqueConstraint("id", name="uq_otp_attempts_id"),
        CheckConstraint("attempts_left >= 0", name="ck_otp_attempts_left_non_negative"),
        CheckConstraint("sent_count_hour >= 0 AND sent_count_day >= 0", name="ck_otp_sent_counts_non_negative"),
        CheckConstraint("fraud_score >= 0", name="ck_otp_fraud_score_non_negative"),
        {"extend_existing": True},
    )

    id = Column(Integer, primary_key=True, autoincrement=True, index=True)
    phone = Column(String(32), nullable=False)
    code_hash = Column(String(255), nullable=False)
    expires_at = Column(DateTime, nullable=False, default=lambda: utc_now() + timedelta(minutes=DEFAULT_OTP_TTL_MIN))

    attempts_left = Column(Integer, default=5, nullable=False)
    is_verified = Column(Boolean, default=False, nullable=False)

    sent_count_hour = Column(Integer, default=0, nullable=False)
    sent_count_day = Column(Integer, default=0, nullable=False)
    last_sent_at = Column(DateTime, nullable=True)
    hour_window_started_at = Column(DateTime, nullable=True)
    day_window_started_at = Column(DateTime, nullable=True)

    purpose = Column(String(32), default=OtpPurpose.LOGIN.value, nullable=False)
    channel = Column(String(16), default=OtpChannel.SMS.value, nullable=False)

    created_at = Column(DateTime, default=utc_now, nullable=False)
    updated_at = Column(DateTime, default=utc_now, onupdate=utc_now, nullable=False)
    deleted_at = Column(DateTime, nullable=True)
    delete_reason = Column(String(64), nullable=True)

    user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    is_blocked = Column(Boolean, default=False, nullable=False)
    blocked_until = Column(DateTime, nullable=True)
    block_reason = Column(String(64), nullable=True)
    fraud_score = Column(Integer, default=0, nullable=False)
    fraud_flags = Column(String(255), nullable=True)

    # Validators
    @validates("phone")
    def _validate_phone(self, _k: str, v: str) -> str:
        v = (v or "").strip()
        if not v:
            raise ValueError("phone must be non-empty")
        if len(v) > 32:
            raise ValueError("phone must be <= 32 chars")
        return v

    @validates("purpose")
    def _v_purpose(self, _k: str, v: Optional[str]) -> str:
        return (v or OtpPurpose.LOGIN.value).strip().lower()

    @validates("channel")
    def _v_channel(self, _k: str, v: Optional[str]) -> str:
        return (v or OtpChannel.SMS.value).strip().lower()

    @validates("expires_at")
    def _v_expires(self, _k: str, v: Optional[datetime]) -> datetime:
        return v or (utc_now() + timedelta(minutes=DEFAULT_OTP_TTL_MIN))

    # Properties
    def is_expired(self) -> bool:
        return utc_now() > (self.expires_at or utc_now())

    def is_deleted(self) -> bool:
        return self.deleted_at is not None

    def is_valid(self) -> bool:
        return (
            not self.is_expired()
            and not self.is_verified
            and self.attempts_left > 0
            and not self.is_deleted()
            and not self.is_currently_blocked()
        )

    @property
    def is_active(self) -> bool:
        return self.is_valid()

    @property
    def is_archived(self) -> bool:
        return (self.delete_reason or "").lower() == "archived"

    @property
    def seconds_left(self) -> int:
        if not self.expires_at:
            return 0
        delta = int((self.expires_at - utc_now()).total_seconds())
        return max(delta, 0)

    # Anti-fraud / Blocking
    def is_currently_blocked(self) -> bool:
        if not self.is_blocked:
            return False
        if self.blocked_until is None:
            return True
        return utc_now() < self.blocked_until

    def should_block(
        self,
        *,
        hourly_limit: int = 5,
        daily_limit: int = 20,
        max_failed_attempts: int = 5,
        fraud_score_threshold: int = 70,
    ) -> Optional[str]:
        if self.sent_count_hour >= hourly_limit or self.sent_count_day >= daily_limit:
            return "rate_limit"
        if self.attempts_left <= 0 or self.attempts_left <= (5 - max_failed_attempts):
            return "attempts"
        if (self.fraud_score or 0) >= fraud_score_threshold:
            return "fraud"
        return None

    def apply_block(self, *, reason: str, minutes: int = 60) -> None:
        self.is_blocked = True
        self.block_reason = reason
        self.blocked_until = (utc_now() + timedelta(minutes=minutes)) if minutes > 0 else None
        self.updated_at = utc_now()
        _emit("otp.block.apply", self.to_audit_dict(extra={"reason": reason, "minutes": minutes}))

    def lift_block(self, *, reason: str = "manual_unblock") -> None:
        self.is_blocked = False
        self.block_reason = reason
        self.blocked_until = None
        self.updated_at = utc_now()
        _emit("otp.block.lift", self.to_audit_dict(extra={"reason": reason}))

    def bump_fraud(self, points: int, *, flag: Optional[str] = None) -> int:
        self.fraud_score = min(max((self.fraud_score or 0) + max(points, 0), 0), 100)
        if flag:
            self.fraud_flags = (self.fraud_flags + f",{flag}") if self.fraud_flags else flag
        self.updated_at = utc_now()
        _emit("otp.fraud.bump", self.to_audit_dict(extra={"points": points, "flag": flag, "score": self.fraud_score}))
        return self.fraud_score

    # Attempts & Verification
    def use_attempt(self) -> bool:
        if self.attempts_left > 0:
            self.attempts_left -= 1
            self.updated_at = utc_now()
            _emit("otp.attempt.use", self.to_audit_dict())
            return True
        _emit("otp.attempt.denied", self.to_audit_dict(extra={"reason": "no_attempts_left"}))
        return False

    def mark_failed_attempt(self) -> None:
        if self.attempts_left > 0:
            self.attempts_left -= 1
        self.updated_at = utc_now()
        _emit("otp.verify.failed", self.to_audit_dict())

    def verify(self, *, auto_archive: bool = True, archive_reason: str = "archived") -> None:
        self.is_verified = True
        self.updated_at = utc_now()
        _emit("otp.verify.success", self.to_audit_dict())
        if auto_archive:
            self.auto_archive_after_verify(reason=archive_reason)

    def verify_with_plain(self, plain_code: str, verifier: Callable[[str, str], bool], *, auto_archive: bool = True) -> bool:
        if not self.is_valid():
            _emit("otp.verify.denied", self.to_audit_dict(extra={"reason": "not_valid"}))
            return False
        ok = False
        try:
            ok = bool(verifier(plain_code, self.code_hash))
        except Exception:
            ok = False
        if ok:
            self.verify(auto_archive=auto_archive)
            return True
        self.mark_failed_attempt()
        return False

    # Rate limiting
    def _reset_hour_if_needed(self, now: Optional[datetime] = None) -> None:
        now = now or utc_now()
        start = self.hour_window_started_at
        if start is None or (now - start) >= timedelta(hours=1):
            self.hour_window_started_at = now
            self.sent_count_hour = 0

    def _reset_day_if_needed(self, now: Optional[datetime] = None) -> None:
        now = now or utc_now()
        start = self.day_window_started_at
        if start is None or (now - start) >= timedelta(days=1):
            self.day_window_started_at = now
            self.sent_count_day = 0

    def reset_counters_if_needed(self) -> None:
        now = utc_now()
        self._reset_hour_if_needed(now)
        self._reset_day_if_needed(now)

    def can_send_now(
        self,
        *,
        hourly_limit: int = 5,
        daily_limit: int = 20,
        block_on_violation_minutes: int = 60,
        auto_block: bool = True,
    ) -> bool:
        if self.is_currently_blocked():
            _emit("otp.send.denied", self.to_audit_dict(extra={"reason": "blocked"}))
            return False

        self.reset_counters_if_needed()
        if self.sent_count_hour >= hourly_limit or self.sent_count_day >= daily_limit:
            if auto_block:
                self.apply_block(reason="rate_limit", minutes=block_on_violation_minutes)
            _emit("otp.send.denied", self.to_audit_dict(extra={"reason": "rate_limit"}))
            return False
        return True

    def register_sent(self) -> None:
        now = utc_now()
        self._reset_hour_if_needed(now)
        self._reset_day_if_needed(now)
        self.sent_count_hour += 1
        self.sent_count_day += 1
        self.last_sent_at = now
        self.updated_at = now
        _emit("otp.send.register", self.to_audit_dict())

    def window_remaining_seconds(self) -> Tuple[int, int]:
        now = utc_now()
        hstart = self.hour_window_started_at
        dstart = self.day_window_started_at
        h_left = 3600 - int((now - hstart).total_seconds()) if hstart else 3600
        d_left = 86400 - int((now - dstart).total_seconds()) if dstart else 86400
        return (max(h_left, 0), max(d_left, 0))

    # Soft delete / Archive
    def soft_delete(self, reason: Optional[str] = None) -> None:
        self.deleted_at = utc_now()
        self.delete_reason = (reason or "").strip() or None
        self.updated_at = utc_now()
        _emit("otp.delete.soft", self.to_audit_dict(extra={"reason": self.delete_reason}))

    def restore(self) -> None:
        self.deleted_at = None
        self.delete_reason = None
        self.updated_at = utc_now()
        _emit("otp.restore", self.to_audit_dict())

    def auto_archive_after_verify(self, *, reason: str = "archived") -> None:
        if self.is_verified and not self.is_deleted():
            self.deleted_at = utc_now()
            self.delete_reason = reason
            self.updated_at = utc_now()
            _emit("otp.archive.after_verify", self.to_audit_dict())

    # Factory / creation
    @classmethod
    def create_new(
        cls,
        phone: str,
        code_hash: str,
        purpose: Union[str, OtpPurpose] = OtpPurpose.LOGIN.value,
        expires_minutes: int = DEFAULT_OTP_TTL_MIN,
        user_id: Optional[int] = None,
        channel: Union[str, OtpChannel] = OtpChannel.SMS.value,
        attempts_left: int = 5,
    ) -> "OtpAttempt":
        now = utc_now()
        purpose_val = purpose.value if isinstance(purpose, OtpPurpose) else str(purpose)
        channel_val = channel.value if isinstance(channel, OtpChannel) else str(channel)
        obj = cls(
            phone=phone,
            code_hash=code_hash,
            purpose=purpose_val,
            channel=channel_val,
            expires_at=now + timedelta(minutes=max(expires_minutes, 1)),
            attempts_left=attempts_left,
            is_verified=False,
            sent_count_hour=0,
            sent_count_day=0,
            user_id=user_id,
            created_at=now,
            updated_at=now,
            hour_window_started_at=now,
            day_window_started_at=now,
        )
        _emit("otp.create", obj.to_audit_dict())
        return obj

    @staticmethod
    def factory(
        *,
        phone: str = "+77001112233",
        code_hash: str = "hash",
        purpose: OtpPurpose = OtpPurpose.LOGIN,
        channel: OtpChannel = OtpChannel.SMS,
        expires_in_sec: int = 300,
        user_id: Optional[int] = None,
        attempts_left: int = 5,
        verified: bool = False,
    ) -> "OtpAttempt":
        now = utc_now()
        obj = OtpAttempt(
            phone=phone,
            code_hash=code_hash,
            purpose=purpose.value,
            channel=channel.value,
            expires_at=now + timedelta(seconds=max(expires_in_sec, 1)),
            attempts_left=attempts_left,
            is_verified=verified,
            user_id=user_id,
            created_at=now,
            updated_at=now,
            hour_window_started_at=now,
            day_window_started_at=now,
        )
        _emit("otp.factory", obj.to_audit_dict())
        return obj

    # Async helpers (session-level)
    @staticmethod
    async def get_latest_active_for_phone(
        session: AsyncSession,
        *,
        phone: str,
        purpose: OtpPurpose = OtpPurpose.LOGIN,
        channel: OtpChannel = OtpChannel.SMS,
    ) -> Optional["OtpAttempt"]:
        rows = await session.execute(
            select(OtpAttempt)
            .where(
                OtpAttempt.phone == phone,
                OtpAttempt.purpose == purpose.value,
                OtpAttempt.channel == channel.value,
                OtpAttempt.deleted_at.is_(None),
                OtpAttempt.is_verified.is_(False),
                OtpAttempt.expires_at > utc_now(),
                OtpAttempt.is_blocked.is_(False),
            )
            .order_by(OtpAttempt.created_at.desc())
            .limit(1)
        )
        return rows.scalar_one_or_none()

    @staticmethod
    async def create_or_replace_latest(
        session: AsyncSession,
        *,
        phone: str,
        code_hash: str,
        purpose: OtpPurpose = OtpPurpose.LOGIN,
        channel: OtpChannel = OtpChannel.SMS,
        ttl_minutes: int = DEFAULT_OTP_TTL_MIN,
        attempts_left: int = 5,
        user_id: Optional[int] = None,
        reset_rate_windows: bool = True,
    ) -> "OtpAttempt":
        old = await OtpAttempt.get_latest_active_for_phone(
            session, phone=phone, purpose=purpose, channel=channel
        )
        if old:
            old.soft_delete("replaced")
        attempt = OtpAttempt.create_new(
            phone=phone,
            code_hash=code_hash,
            purpose=purpose,
            channel=channel,
            expires_minutes=ttl_minutes,
            attempts_left=attempts_left,
            user_id=user_id,
        )
        if reset_rate_windows:
            now = utc_now()
            attempt.hour_window_started_at = now
            attempt.day_window_started_at = now
            attempt.sent_count_hour = 0
            attempt.sent_count_day = 0
        session.add(attempt)
        await session.flush()
        _emit("otp.create.replace", attempt.to_audit_dict())
        return attempt

    async def rotate_code(
        self, session: AsyncSession, *, new_code_hash: str, ttl_minutes: int = DEFAULT_OTP_TTL_MIN
    ) -> None:
        self.code_hash = new_code_hash
        self.expires_at = utc_now() + timedelta(minutes=max(ttl_minutes, 1))
        self.updated_at = utc_now()
        self.reset_counters_if_needed()
        await session.flush()
        _emit("otp.code.rotate", self.to_audit_dict())

    # Maintenance
    @staticmethod
    async def cleanup_verified(session: AsyncSession, *, older_than_days: int = 7, reason: str = "archived") -> int:
        threshold = utc_now() - timedelta(days=older_than_days)
        res = await session.execute(
            update(OtpAttempt)
            .where(
                OtpAttempt.is_verified.is_(True),
                OtpAttempt.created_at < threshold,
                OtpAttempt.deleted_at.is_(None),
            )
            .values(deleted_at=utc_now(), delete_reason=reason, updated_at=utc_now())
        )
        count = int(res.rowcount or 0)
        if count:
            _emit("otp.cleanup.verified", {"count": count, "older_than_days": older_than_days})
        return count

    @staticmethod
    async def cleanup_unverified_expired(
        session: AsyncSession, *, older_than_minutes: int = 60, reason: str = "expired_cleanup"
    ) -> int:
        threshold = utc_now() - timedelta(minutes=older_than_minutes)
        res = await session.execute(
            update(OtpAttempt)
            .where(
                OtpAttempt.is_verified.is_(False),
                OtpAttempt.expires_at < threshold,
                OtpAttempt.deleted_at.is_(None),
            )
            .values(deleted_at=utc_now(), delete_reason=reason, updated_at=utc_now())
        )
        count = int(res.rowcount or 0)
        if count:
            _emit("otp.cleanup.unverified_expired", {"count": count, "older_than_minutes": older_than_minutes})
        return count

    @staticmethod
    async def purge_soft_deleted_verified(session: AsyncSession, *, older_than_days: int = 30) -> int:
        threshold = utc_now() - timedelta(days=older_than_days)
        res = await session.execute(
            delete(OtpAttempt).where(
                OtpAttempt.deleted_at.is_not(None),
                OtpAttempt.is_verified.is_(True),
                OtpAttempt.deleted_at < threshold,
            )
        )
        count = int(res.rowcount or 0)
        if count:
            _emit("otp.purge.soft_deleted_verified", {"count": count, "older_than_days": older_than_days})
        return count

    @staticmethod
    async def cleanup_expired(session: AsyncSession, *, older_than_minutes: int = 60) -> int:
        threshold = utc_now() - timedelta(minutes=older_than_minutes)
        res = await session.execute(
            update(OtpAttempt)
            .where(OtpAttempt.expires_at < threshold, OtpAttempt.deleted_at.is_(None))
            .values(deleted_at=utc_now(), delete_reason="expired_cleanup", updated_at=utc_now())
        )
        count = int(res.rowcount or 0)
        if count:
            _emit("otp.cleanup.expired", {"count": count, "older_than_minutes": older_than_minutes})
        return count

    @staticmethod
    async def purge_soft_deleted(session: AsyncSession, *, older_than_days: int = 7) -> int:
        threshold = utc_now() - timedelta(days=older_than_days)
        res = await session.execute(
            delete(OtpAttempt).where(OtpAttempt.deleted_at.is_not(None), OtpAttempt.deleted_at < threshold)
        )
        count = int(res.rowcount or 0)
        if count:
            _emit("otp.purge.soft_deleted", {"count": count, "older_than_days": older_than_days})
        return count

    @staticmethod
    async def archive_old(session: AsyncSession, *, older_than_days: int = 30) -> int:
        threshold = utc_now() - timedelta(days=older_than_days)
        res = await session.execute(
            update(OtpAttempt)
            .where(
                OtpAttempt.created_at < threshold,
                OtpAttempt.deleted_at.is_(None),
                or_(OtpAttempt.is_verified.is_(True), OtpAttempt.expires_at < utc_now()),
            )
            .values(deleted_at=utc_now(), delete_reason="archived_old", updated_at=utc_now())
        )
        count = int(res.rowcount or 0)
        if count:
            _emit("otp.archive.old", {"count": count, "older_than_days": older_than_days})
        return count

    @staticmethod
    async def bulk_soft_delete(session: AsyncSession, ids: Sequence[int], *, reason: str = "bulk_soft_delete") -> int:
        if not ids:
            return 0
        res = await session.execute(
            update(OtpAttempt)
            .where(OtpAttempt.id.in_(list(ids)), OtpAttempt.deleted_at.is_(None))
            .values(deleted_at=utc_now(), delete_reason=reason, updated_at=utc_now())
        )
        count = int(res.rowcount or 0)
        if count:
            _emit("otp.bulk.soft_delete", {"count": count, "ids": list(ids)})
        return count

    @staticmethod
    async def bulk_restore(session: AsyncSession, ids: Sequence[int]) -> int:
        if not ids:
            return 0
        res = await session.execute(
            update(OtpAttempt)
            .where(OtpAttempt.id.in_(list(ids)))
            .values(deleted_at=None, delete_reason=None, updated_at=utc_now())
        )
        count = int(res.rowcount or 0)
        if count:
            _emit("otp.bulk.restore", {"count": count, "ids": list(ids)})
        return count

    # Queries
    @staticmethod
    async def count_sent_today(
        session: AsyncSession, *, phone: str, purpose: OtpPurpose = OtpPurpose.LOGIN, channel: OtpChannel = OtpChannel.SMS
    ) -> int:
        since = utc_now() - timedelta(days=1)
        rows = await session.execute(
            select(func.coalesce(func.sum(OtpAttempt.sent_count_day), 0)).where(
                OtpAttempt.phone == phone,
                OtpAttempt.purpose == purpose.value,
                OtpAttempt.channel == channel.value,
                OtpAttempt.created_at >= since,
                OtpAttempt.deleted_at.is_(None),
            )
        )
        return int(rows.scalar_one() or 0)

    # Audit / Serialization
    @staticmethod
    def configure_audit_sink(sink: Optional[Callable[[str, Dict[str, Any]], None]]) -> None:
        configure_audit_sink(sink)

    def emit_audit_event(self, event_name: str, *, extra: Optional[Dict[str, Any]] = None) -> None:
        _emit(event_name, self.to_audit_dict(extra=extra))

    def to_audit_dict(self, *, extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        payload = {
            "id": self.id,
            "phone": self.phone,
            "purpose": self.purpose,
            "channel": self.channel,
            "is_verified": self.is_verified,
            "attempts_left": self.attempts_left,
            "sent_count_hour": self.sent_count_hour,
            "sent_count_day": self.sent_count_day,
            "is_blocked": self.is_blocked,
            "blocked_until": self.blocked_until.isoformat() if self.blocked_until else None,
            "block_reason": self.block_reason,
            "fraud_score": self.fraud_score,
            "fraud_flags": self.fraud_flags,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "deleted_at": self.deleted_at.isoformat() if self.deleted_at else None,
            "delete_reason": self.delete_reason,
            "user_id": self.user_id,
        }
        if extra:
            payload["extra"] = extra
        return payload

    def to_dict(self) -> Dict[str, Any]:
        base = self.to_audit_dict()
        base["seconds_left"] = self.seconds_left
        base["is_archived"] = self.is_archived
        return base

    def to_public_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "phone": self.phone,
            "purpose": self.purpose,
            "channel": self.channel,
            "is_verified": self.is_verified,
            "is_blocked": self.is_blocked,
            "blocked_until": self.blocked_until.isoformat() if self.blocked_until else None,
            "seconds_left": self.seconds_left,
            "is_archived": self.is_archived,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<OtpAttempt id={self.id} phone='{self.phone}' purpose='{self.purpose}' "
            f"channel='{self.channel}' exp={self.expires_at} attempts_left={self.attempts_left} "
            f"verified={self.is_verified} blocked={self.is_blocked}>"
        )


__all__ = [
    "OTPCode",
    "OtpAttempt",
    "OtpPurpose",
    "OtpChannel",
    "utc_now",
    "configure_audit_sink",
    "get_audit_sink",
]
