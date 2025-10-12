# app/models/inventory_outbox.py
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Literal, Optional

from sqlalchemy import CheckConstraint, DateTime, Index, Integer, String, Text, inspect, or_, select
from sqlalchemy.exc import OperationalError, ProgrammingError
from sqlalchemy.orm import Mapped, Session, mapped_column

try:
    # SQLAlchemy >=2.x
    from sqlalchemy.exc import NoSuchTableError
except Exception:  # pragma: no cover

    class NoSuchTableError(Exception):  # type: ignore
        ...


from app.models.base import Base, BaseModel, SoftDeleteMixin
from app.models.types import JSONBCompat

log = logging.getLogger(__name__)

OutboxStatus = Literal["pending", "sent", "failed"]
OutboxChannel = Literal["erp", "marketplace", "webhook", "email", "task"]


class InventoryOutbox(BaseModel, SoftDeleteMixin):
    """
    Inventory Outbox (паттерн Outbox) — событийная очередь для интеграций.

    Ключевые решения:
      - Кросс-СУБД JSON в `payload` через JSONBCompat (PostgreSQL/SQLite).
      - Без внешних ключей: агрегаты могут удаляться независимо; запись остаётся для аудита.
      - Идемпотентность/ретраи: status/attempts/next_attempt_at.
      - created_at/updated_at — из BaseModel; soft delete — из SoftDeleteMixin.
    """

    __tablename__ = "inventory_outbox"
    __allow_unmapped__ = True

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)

    aggregate_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    # строковый ID — принимаем int/uuid/строку, приводим к строке
    aggregate_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)

    payload: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONBCompat, nullable=True)

    channel: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending", index=True)

    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_attempt_at: Mapped[Optional[datetime]] = mapped_column(DateTime, index=True)
    last_error: Mapped[Optional[str]] = mapped_column(Text)

    processed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, index=True)

    __table_args__ = (
        # Быстрый выбор «ожидающих» (due) событий
        Index("ix_inv_outbox_status_due", "status", "next_attempt_at"),
        # Детерминированный порядок для чтения/аналитики
        Index("ix_inv_outbox_aggregate_created", "aggregate_type", "aggregate_id", "created_at"),
        CheckConstraint("attempts >= 0", name="ck_inv_outbox_attempts_nonneg"),
        CheckConstraint(
            "status in ('pending','sent','failed')",
            name="ck_inv_outbox_status_allowed",
        ),
    )

    # -------------------------------------------------------------------------
    # Инспекция/служебные методы
    # -------------------------------------------------------------------------
    @staticmethod
    def is_present_in_metadata() -> bool:
        """Присутствует ли таблица в Base.metadata (не путать с БД)."""
        try:
            return "inventory_outbox" in Base.metadata.tables
        except Exception:
            return False

    @staticmethod
    def table_exists_in_db(session: Session) -> bool:
        """Осторожная проверка существования таблицы в БД (без падений)."""
        try:
            bind = session.get_bind()
            if not bind:
                return False
            insp = inspect(bind)
            return insp.has_table("inventory_outbox")
        except Exception:
            return False

    @staticmethod
    def ensure_imported() -> None:
        """Гарантировано импортирует модуль, чтобы класс попал в Registry/metadata."""
        try:
            from app.models import inventory_outbox as _  # noqa: F401
        except Exception as e:  # pragma: no cover
            log.debug("ensure_imported: failed to import inventory_outbox: %s", e)

    # -------------------------------------------------------------------------
    # Основные операции
    # -------------------------------------------------------------------------
    @staticmethod
    def enqueue(
        session: Session,
        *,
        aggregate_type: str,
        aggregate_id: int | str,
        event_type: str,
        payload: dict[str, Any] | None = None,
        channel: Optional[str] = None,
        status: OutboxStatus = "pending",
        next_attempt_in_seconds: Optional[int] = None,
    ) -> InventoryOutbox:
        """
        Жёсткая постановка события в Outbox.
        Ожидает, что таблица существует в БД (иначе упадёт на flush/commit).
        """
        ev = InventoryOutbox(
            aggregate_type=(aggregate_type or "").strip(),
            aggregate_id=str(aggregate_id),
            event_type=(event_type or "").strip(),
            payload=payload or {},
            channel=(channel or None),
            status=status or "pending",
            attempts=0,
        )
        if next_attempt_in_seconds is not None:
            ev.next_attempt_at = datetime.utcnow() + timedelta(seconds=int(next_attempt_in_seconds))
        session.add(ev)
        return ev

    @staticmethod
    def safe_enqueue(
        session: Session,
        *,
        aggregate_type: str,
        aggregate_id: int | str,
        event_type: str,
        payload: dict[str, Any] | None = None,
        channel: Optional[str] = "erp",
        status: OutboxStatus = "pending",
        next_attempt_in_seconds: Optional[int] = None,
        log_prefix: str = "InventoryOutbox",
    ) -> Optional[InventoryOutbox]:
        """
        Безопасная постановка события (не ломает основной бизнес-флоу):

          1) Если таблицы нет в metadata — логируем INFO и пропускаем.
          2) Пытаемся вставить внутри SAVEPOINT (begin_nested) + flush().
             При любой DB-ошибке откатываем только savepoint и удаляем объект
             из сессии (expunge), чтобы он не попал в общий commit().
        """
        if not InventoryOutbox.is_present_in_metadata():
            log.info("%s: table not present in metadata — skipping enqueue.", log_prefix)
            return None

        ev: Optional[InventoryOutbox] = None
        try:
            with session.begin_nested():  # savepoint (SQLite/PG поддерживают)
                ev = InventoryOutbox(
                    aggregate_type=(aggregate_type or "").strip(),
                    aggregate_id=str(aggregate_id),
                    event_type=(event_type or "").strip(),
                    payload=payload or {},
                    channel=(channel or None),
                    status=status or "pending",
                    attempts=0,
                )
                if next_attempt_in_seconds is not None:
                    ev.next_attempt_at = datetime.utcnow() + timedelta(
                        seconds=int(next_attempt_in_seconds)
                    )
                session.add(ev)
                # Ключевой момент: flush сразу, чтобы поймать «no such table»
                session.flush()
            return ev
        except (OperationalError, ProgrammingError, NoSuchTableError) as db_err:
            if ev is not None:
                try:
                    session.expunge(ev)
                except Exception:
                    pass
            log.warning("%s: failed to enqueue (skipped): %s", log_prefix, db_err)
            return None
        except Exception as err:
            if ev is not None:
                try:
                    session.expunge(ev)
                except Exception:
                    pass
            log.warning("%s: unexpected error during enqueue (skipped): %s", log_prefix, err)
            return None

    @staticmethod
    def batch_fetch(
        session: Session,
        *,
        status: OutboxStatus = "pending",
        aggregate_type: Optional[str] = None,
        limit: int = 1000,
        due_only: bool = False,
        channel: Optional[str] = None,
        order_by_created_asc: bool = True,
    ) -> list[InventoryOutbox]:
        """
        Получить пачку событий.
        - due_only=True вернёт только «готовые к попытке» (next_attempt_at IS NULL или <= now).
        """
        q = select(InventoryOutbox).where(InventoryOutbox.status == (status or "pending"))
        if aggregate_type:
            q = q.where(InventoryOutbox.aggregate_type == aggregate_type)
        if channel:
            q = q.where(InventoryOutbox.channel == channel)
        if due_only:
            # используем python-now для кросс-СУБД сопоставимости
            q = q.where(
                or_(
                    InventoryOutbox.next_attempt_at.is_(None),
                    InventoryOutbox.next_attempt_at <= datetime.utcnow(),
                )
            )
        q = q.limit(max(1, int(limit)))
        q = q.order_by(
            InventoryOutbox.created_at.asc()
            if order_by_created_asc
            else InventoryOutbox.created_at.desc()
        )
        return list(session.execute(q).scalars().all())

    # -------------------------------------------------------------------------
    # Workflow-метки
    # -------------------------------------------------------------------------
    def mark_sent(self, *, when: Optional[datetime] = None) -> None:
        """Отметить событие как успешно доставленное."""
        self.status = "sent"
        self.processed_at = when or datetime.utcnow()
        self.last_error = None
        self.next_attempt_at = None

    def mark_failed(self, err: str, *, retry_in_seconds: int = 60) -> None:
        """Отметить событие как неуспешное и запланировать повторную попытку."""
        self.status = "failed"
        self.attempts = int(self.attempts or 0) + 1
        self.last_error = (err or "").strip() or None
        self.next_attempt_at = datetime.utcnow() + timedelta(seconds=int(retry_in_seconds))

    def set_pending(self, *, delay_seconds: int | None = None) -> None:
        """Вернуть событие в очередь (для ручного ретрая и т.п.)."""
        self.status = "pending"
        self.last_error = None
        if delay_seconds is None:
            self.next_attempt_at = None
        else:
            self.next_attempt_at = datetime.utcnow() + timedelta(seconds=int(delay_seconds))

    # -------------------------------------------------------------------------
    # Представление/диагностика
    # -------------------------------------------------------------------------
    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<InventoryOutbox id={self.id} agg={self.aggregate_type}:{self.aggregate_id} "
            f"type={self.event_type} status={self.status} attempts={self.attempts}>"
        )

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "aggregate_type": self.aggregate_type,
            "aggregate_id": self.aggregate_id,
            "event_type": self.event_type,
            "payload": self.payload or {},
            "channel": self.channel,
            "status": self.status,
            "attempts": int(self.attempts or 0),
            "next_attempt_at": self.next_attempt_at.isoformat(timespec="seconds")
            if self.next_attempt_at
            else None,
            "last_error": self.last_error,
            "processed_at": self.processed_at.isoformat(timespec="seconds")
            if self.processed_at
            else None,
            "created_at": self.created_at.isoformat(timespec="seconds")
            if getattr(self, "created_at", None)
            else None,
            "updated_at": self.updated_at.isoformat(timespec="seconds")
            if getattr(self, "updated_at", None)
            else None,
        }

    # ---------- test/seed factory ----------
    @classmethod
    def factory(
        cls,
        *,
        aggregate_type: str = "product_stock",
        aggregate_id: str | int = "1",
        event_type: str = "stock.updated",
        payload: Optional[dict[str, Any]] = None,
        channel: Optional[str] = "erp",
        status: OutboxStatus = "pending",
    ) -> InventoryOutbox:
        return cls(
            aggregate_type=aggregate_type,
            aggregate_id=str(aggregate_id),
            event_type=event_type,
            payload=payload or {"ok": True},
            channel=channel,
            status=status,
            attempts=0,
        )


__all__ = ["InventoryOutbox"]
