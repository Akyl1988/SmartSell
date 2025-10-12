"""
Campaign & Message models for marketing campaigns and messaging.

— Совместимо с базовой системой из PR #21 (app.models.base.Base).
— PostgreSQL-первичный таргет (поддерживаются и другие СУБД).

Добавлено/расширено:
    • Soft-delete через миксин с логированием удаления/восстановлением.
    • Highload-операции: bulk insert / bulk status updates.
    • Расширенные каналы (push, viber и т.п.).
    • Поля ошибок: error_code (для интеграций) + error_message.
    • Аналитика: агрегации по статусам/каналам/времени (sent_at/delivered_at),
      а также по времени удаления (deleted_at).
    • Экспорт для BI: в pandas.DataFrame, CSV, Parquet.
    • Интеграция с ML: выгрузка feature-набора для оценки качества доставки.
    • Фабрики/генераторы тестовых данных (Campaign/Message) + удобные async-хелперы.
    • Авто-очистка/архивация soft-deleted сообщений для фоновой задачи.
    • Property Message.is_archived и кампанийные массовые archive/cleanup-методы.

Важные принципы:
    - Все времена timezone-aware (UTC).
    - Мультиарендность — company_id.
    - ENUM — нативные PostgreSQL с именами типов, с fallback CHECK в других СУБД.
    - Денормализованные счётчики на Campaign для быстрых витрин.
"""

from __future__ import annotations

import enum
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any, Optional

from sqlalchemy import CheckConstraint, DateTime
from sqlalchemy import Enum as SAEnum
from sqlalchemy import (
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    delete,
    func,
    select,
    update,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates

from app.models.base import Base  # PR #21 Base

# -----------------------------------------------------------------------------
# Константы / утилиты
# -----------------------------------------------------------------------------
MAX_TITLE_LEN = 255
MAX_RECIPIENT_LEN = 255
MAX_CONTENT_LEN = 2000
MAX_ERROR_CODE_LEN = 64


def utc_now() -> datetime:
    return datetime.now(UTC)


# -----------------------------------------------------------------------------
# Soft-delete Mixin
# -----------------------------------------------------------------------------
class SoftDeleteMixin:
    """
    Универсальный soft-delete.
    Глобальный "исключающий" фильтр не навязывается — применяйте в репозитории/DAO.
    """

    deleted_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    deleted_by: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    delete_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None

    def soft_delete(
        self, *, by_user_id: Optional[int] = None, reason: Optional[str] = None
    ) -> None:
        self.deleted_at = utc_now()
        self.deleted_by = by_user_id
        self.delete_reason = (reason or "").strip() or None

    def restore(self) -> None:
        self.deleted_at = None
        self.deleted_by = None
        self.delete_reason = None


# -----------------------------------------------------------------------------
# ENUMS
# -----------------------------------------------------------------------------
class CampaignStatus(str, enum.Enum):
    DRAFT = "draft"
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"


class MessageStatus(str, enum.Enum):
    PENDING = "pending"
    SENT = "sent"
    DELIVERED = "delivered"
    FAILED = "failed"


class ChannelType(str, enum.Enum):
    EMAIL = "email"
    WHATSAPP = "whatsapp"
    TELEGRAM = "telegram"
    SMS = "sms"
    PUSH = "push"
    VIBER = "viber"
    # можно расширять дальше при необходимости (e.g., INSTAGRAM_DM = "instagram_dm")


# -----------------------------------------------------------------------------
# Campaign
# -----------------------------------------------------------------------------
class Campaign(SoftDeleteMixin, Base):
    __tablename__ = "campaigns"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)  # сохраняем совместимость

    title: Mapped[str] = mapped_column(String(MAX_TITLE_LEN), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    status: Mapped[CampaignStatus] = mapped_column(
        SAEnum(
            CampaignStatus,
            name="campaign_status",
            native_enum=True,
            create_constraint=False,
            validate_strings=True,
        ),
        default=CampaignStatus.DRAFT,
        nullable=False,
    )

    scheduled_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )

    # Multitenant
    company_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    company = relationship("Company", back_populates="campaigns")

    # Денормализация
    total_messages: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    sent_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    delivered_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    messages: Mapped[list[Message]] = relationship(
        "Message",
        back_populates="campaign",
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="selectin",
    )

    __table_args__ = (
        CheckConstraint("length(title) > 0", name="ck_campaign_title_non_empty"),
        Index("ix_campaign_company_status", "company_id", "status"),
        Index("ix_campaign_scheduled_at_status", "scheduled_at", "status"),
        UniqueConstraint(
            "company_id", "title", "scheduled_at", name="uq_campaign_company_title_scheduled"
        ),
    )

    # --------- Валидации ---------
    @validates("title")
    def _validate_title(self, _k: str, v: str) -> str:
        v = (v or "").strip()
        if not v:
            raise ValueError("Campaign.title must be non-empty.")
        if len(v) > MAX_TITLE_LEN:
            raise ValueError(f"Campaign.title must be <= {MAX_TITLE_LEN} chars.")
        return v

    # --------- Свойства ---------
    @property
    def is_draft(self) -> bool:
        return self.status == CampaignStatus.DRAFT

    @property
    def is_active(self) -> bool:
        return self.status == CampaignStatus.ACTIVE

    @property
    def is_paused(self) -> bool:
        return self.status == CampaignStatus.PAUSED

    @property
    def is_completed(self) -> bool:
        return self.status == CampaignStatus.COMPLETED

    @property
    def is_scheduled(self) -> bool:
        return self.scheduled_at is not None

    # --------- Бизнес-методы ---------
    def schedule(self, when_utc: datetime) -> None:
        if when_utc.tzinfo is None:
            raise ValueError("schedule(): when_utc must be timezone-aware (UTC).")
        self.scheduled_at = when_utc

    def activate(self) -> None:
        if self.is_completed:
            raise ValueError("Нельзя активировать завершённую кампанию.")
        self.status = CampaignStatus.ACTIVE

    def pause(self) -> None:
        if self.is_completed:
            raise ValueError("Нельзя ставить на паузу завершённую кампанию.")
        self.status = CampaignStatus.PAUSED

    def complete(self) -> None:
        self.status = CampaignStatus.COMPLETED

    def add_message(
        self,
        *,
        recipient: str,
        content: str,
        channel: ChannelType = ChannelType.EMAIL,
        status: MessageStatus = MessageStatus.PENDING,
        provider_message_id: Optional[str] = None,
        error_code: Optional[str] = None,
    ) -> Message:
        msg = Message(
            campaign=self,
            recipient=recipient,
            content=content,
            status=status,
            channel=channel,
            provider_message_id=provider_message_id,
            error_code=error_code,
        )
        self.messages.append(msg)
        self.total_messages += 1
        return msg

    def bulk_add_messages(
        self,
        recipients: Iterable[str],
        *,
        content_template: str,
        template_vars_iter: Optional[Iterable[dict[str, Any]]] = None,
        channel: ChannelType = ChannelType.EMAIL,
    ) -> list[Message]:
        msgs: list[Message] = []
        if template_vars_iter is None:
            template_vars_iter = [{} for _ in recipients]
        for recipient, vars_ in zip(recipients, template_vars_iter):
            content = Message.render_content_static(content_template, vars_ or {})
            msgs.append(self.add_message(recipient=recipient, content=content, channel=channel))
        return msgs

    @staticmethod
    async def bulk_insert_messages_async(
        session,
        campaign_id: int,
        items: Iterable[dict[str, Any]],
    ) -> int:
        """
        Highload-вставка сообщений через bulk_insert_mappings.
        items: {recipient, content, channel?, status?, provider_message_id?, error_code?}
        """
        rows: list[dict[str, Any]] = []
        for it in items:
            rows.append(
                {
                    "campaign_id": campaign_id,
                    "recipient": it["recipient"],
                    "content": it["content"],
                    "channel": it.get("channel", ChannelType.EMAIL),
                    "status": it.get("status", MessageStatus.PENDING),
                    "provider_message_id": it.get("provider_message_id"),
                    "error_code": it.get("error_code"),
                    "sent_at": None,
                    "delivered_at": None,
                    "deleted_at": None,
                    "deleted_by": None,
                    "delete_reason": None,
                }
            )
        if not rows:
            return 0
        session.bulk_insert_mappings(Message, rows)
        await session.execute(
            update(Campaign)
            .where(Campaign.id == campaign_id)
            .values(total_messages=Campaign.total_messages + len(rows))
        )
        return len(rows)

    async def refresh_counters_async(self, session) -> None:
        """
        Пересчитать счётчики по не удалённым сообщениям.
        """
        total = await session.scalar(
            select(func.count(Message.id)).where(
                Message.campaign_id == self.id, Message.deleted_at.is_(None)
            )
        )
        sent = await session.scalar(
            select(func.count(Message.id)).where(
                Message.campaign_id == self.id,
                Message.status == MessageStatus.SENT,
                Message.deleted_at.is_(None),
            )
        )
        delivered = await session.scalar(
            select(func.count(Message.id)).where(
                Message.campaign_id == self.id,
                Message.status == MessageStatus.DELIVERED,
                Message.deleted_at.is_(None),
            )
        )
        failed = await session.scalar(
            select(func.count(Message.id)).where(
                Message.campaign_id == self.id,
                Message.status == MessageStatus.FAILED,
                Message.deleted_at.is_(None),
            )
        )
        self.total_messages = int(total or 0)
        self.sent_count = int(sent or 0)
        self.delivered_count = int(delivered or 0)
        self.failed_count = int(failed or 0)

    # --------- Аналитика ---------
    @staticmethod
    async def aggregate_by_status_async(session, campaign_id: int) -> dict[str, int]:
        rows = await session.execute(
            select(Message.status, func.count(Message.id))
            .where(Message.campaign_id == campaign_id, Message.deleted_at.is_(None))
            .group_by(Message.status)
        )
        return {status.value: int(cnt) for status, cnt in rows.all()}

    @staticmethod
    async def aggregate_by_channel_async(session, campaign_id: int) -> dict[str, int]:
        rows = await session.execute(
            select(Message.channel, func.count(Message.id))
            .where(Message.campaign_id == campaign_id, Message.deleted_at.is_(None))
            .group_by(Message.channel)
        )
        return {ch.value: int(cnt) for ch, cnt in rows.all()}

    @staticmethod
    async def aggregate_time_series_async(
        session,
        campaign_id: int,
        *,
        bucket: str = "day",  # 'hour' | 'day'
        field: str = "sent_at",  # 'sent_at' | 'delivered_at'
        include_deleted: bool = False,  # учитывать ли удалённые сообщения
    ) -> list[tuple[str, int]]:
        """
        Вернёт [(label, count)], где label — YYYY-MM-DD или YYYY-MM-DD HH:00.
        """
        if field not in {"sent_at", "delivered_at"}:
            raise ValueError("field must be 'sent_at' or 'delivered_at'")
        ts_col = getattr(Message, field)

        if bucket == "hour":
            bucket_expr = func.date_trunc("hour", ts_col)
            fmt = "%Y-%m-%d %H:00"
        elif bucket == "day":
            bucket_expr = func.date_trunc("day", ts_col)
            fmt = "%Y-%m-%d"
        else:
            raise ValueError("bucket must be 'hour' or 'day'")

        where = [Message.campaign_id == campaign_id, ts_col.is_not(None)]
        if not include_deleted:
            where.append(Message.deleted_at.is_(None))

        rows = await session.execute(
            select(func.to_char(bucket_expr, fmt), func.count(Message.id))
            .where(*where)
            .group_by(bucket_expr)
            .order_by(bucket_expr.asc())
        )
        return [(str(label), int(cnt)) for label, cnt in rows.all()]

    @staticmethod
    async def aggregate_deleted_time_series_async(
        session,
        campaign_id: int,
        *,
        bucket: str = "day",  # 'hour' | 'day'
        only_failed: bool = False,  # динамика ошибок среди удалённых
    ) -> list[tuple[str, int]]:
        """
        Агрегация по времени удаления (deleted_at). Полезно для мониторинга "уборок".
        """
        if bucket == "hour":
            bucket_expr = func.date_trunc("hour", Message.deleted_at)
            fmt = "%Y-%m-%d %H:00"
        elif bucket == "day":
            bucket_expr = func.date_trunc("day", Message.deleted_at)
            fmt = "%Y-%m-%d"
        else:
            raise ValueError("bucket must be 'hour' or 'day'")

        where = [Message.campaign_id == campaign_id, Message.deleted_at.is_not(None)]
        if only_failed:
            where.append(Message.status == MessageStatus.FAILED)

        rows = await session.execute(
            select(func.to_char(bucket_expr, fmt), func.count(Message.id))
            .where(*where)
            .group_by(bucket_expr)
            .order_by(bucket_expr.asc())
        )
        return [(str(label), int(cnt)) for label, cnt in rows.all()]

    # --------- Экспорт в pandas/CSV/Parquet ---------
    @staticmethod
    async def to_dataframe_async(session, campaign_id: int, include_deleted: bool = False):
        """
        Экспорт сообщений кампании в pandas.DataFrame. Если pandas недоступен — поднимет ImportError.
        """
        try:
            import pandas as pd  # type: ignore
        except Exception as e:  # pragma: no cover
            raise ImportError("pandas is required for to_dataframe_async()") from e

        where = [Message.campaign_id == campaign_id]
        if not include_deleted:
            where.append(Message.deleted_at.is_(None))

        rows = await session.execute(
            select(
                Message.id,
                Message.campaign_id,
                Message.recipient,
                Message.content,
                Message.status,
                Message.channel,
                Message.provider_message_id,
                Message.sent_at,
                Message.delivered_at,
                Message.error_code,
                Message.error_message,
                Message.deleted_at,
                Message.deleted_by,
                Message.delete_reason,
            ).where(*where)
        )
        data = []
        for r in rows.all():
            status = r.status.value if isinstance(r.status, enum.Enum) else r.status
            channel = r.channel.value if isinstance(r.channel, enum.Enum) else r.channel
            data.append(
                {
                    "id": r.id,
                    "campaign_id": r.campaign_id,
                    "recipient": r.recipient,
                    "content": r.content,
                    "status": status,
                    "channel": channel,
                    "provider_message_id": r.provider_message_id,
                    "sent_at": r.sent_at,
                    "delivered_at": r.delivered_at,
                    "error_code": r.error_code,
                    "error_message": r.error_message,
                    "deleted_at": r.deleted_at,
                    "deleted_by": r.deleted_by,
                    "delete_reason": r.delete_reason,
                }
            )
        return pd.DataFrame(data)

    @staticmethod
    async def to_csv_async(
        session,
        campaign_id: int,
        path: str,
        include_deleted: bool = False,
        index: bool = False,
        **to_csv_kwargs: Any,
    ) -> str:
        """
        Сериализация данных кампании в CSV на диск.
        Возвращает путь к файлу.
        """
        df = await Campaign.to_dataframe_async(session, campaign_id, include_deleted)
        df.to_csv(path, index=index, **to_csv_kwargs)
        return path

    @staticmethod
    async def to_parquet_async(
        session,
        campaign_id: int,
        path: str,
        include_deleted: bool = False,
        engine: str = "pyarrow",
        **to_parquet_kwargs: Any,
    ) -> str:
        """
        Сериализация данных кампании в Parquet на диск.
        Требует pandas + (pyarrow|fastparquet).
        Возвращает путь к файлу.
        """
        df = await Campaign.to_dataframe_async(session, campaign_id, include_deleted)
        df.to_parquet(path, engine=engine, **to_parquet_kwargs)
        return path

    # --------- ML-признаки ---------
    @staticmethod
    async def to_features_dataframe_async(
        session,
        campaign_id: int,
        include_deleted: bool = False,
    ):
        """
        Feature-набор для анализа качества доставки.
        Возвращает pandas.DataFrame со столбцами:
          - id, channel, status
          - content_len
          - sent_hour (0-23), delivered_hour (0-23)
          - delivery_latency_sec (delivered_at - sent_at)
          - has_error_code (0/1), has_error_message (0/1), is_failed (0/1), is_deleted (0/1)
        """
        try:
            import pandas as pd  # type: ignore
        except Exception as e:  # pragma: no cover
            raise ImportError("pandas is required for to_features_dataframe_async()") from e

        where = [Message.campaign_id == campaign_id]
        if not include_deleted:
            where.append(Message.deleted_at.is_(None))

        rows = await session.execute(
            select(
                Message.id,
                Message.status,
                Message.channel,
                Message.content,
                Message.sent_at,
                Message.delivered_at,
                Message.error_code,
                Message.error_message,
                Message.deleted_at,
            ).where(*where)
        )
        data = []
        for r in rows.all():
            status_val = r.status.value if isinstance(r.status, enum.Enum) else r.status
            channel_val = r.channel.value if isinstance(r.channel, enum.Enum) else r.channel
            content_len = len(r.content or "")
            sent_hour = r.sent_at.hour if r.sent_at else None
            delivered_hour = r.delivered_at.hour if r.delivered_at else None
            latency = (
                (r.delivered_at - r.sent_at).total_seconds()
                if (r.sent_at and r.delivered_at)
                else None
            )
            data.append(
                {
                    "id": r.id,
                    "channel": channel_val,
                    "status": status_val,
                    "content_len": content_len,
                    "sent_hour": sent_hour,
                    "delivered_hour": delivered_hour,
                    "delivery_latency_sec": latency,
                    "has_error_code": 1 if r.error_code else 0,
                    "has_error_message": 1 if (r.error_message and r.error_message.strip()) else 0,
                    "is_failed": 1 if status_val == MessageStatus.FAILED.value else 0,
                    "is_deleted": 1 if r.deleted_at else 0,
                }
            )
        return pd.DataFrame(data)

    # --------- Массовая архивация/очистка в разрезе кампании ---------
    async def archive_messages_async(
        self,
        session,
        *,
        older_than: timedelta = timedelta(days=7),
        only_failed: bool = False,
        archived_reason: str = "archived",
    ) -> int:
        """
        Проставляет delete_reason='archived' у сообщений кампании,
        помеченных как удалённые и старше older_than. Возвращает число обновлённых строк.
        """
        cutoff = utc_now() - older_than
        conds = [
            Message.campaign_id == self.id,
            Message.deleted_at.is_not(None),
            Message.deleted_at < cutoff,
            (Message.delete_reason.is_(None)) | (Message.delete_reason == ""),
        ]
        if only_failed:
            conds.append(Message.status == MessageStatus.FAILED)
        res = await session.execute(
            update(Message).where(*conds).values(delete_reason=archived_reason)
        )
        return int(res.rowcount or 0)

    async def purge_messages_async(
        self,
        session,
        *,
        older_than: timedelta = timedelta(days=30),
        only_archived: bool = False,
    ) -> int:
        """
        Жёсткое удаление soft-deleted сообщений кампании, старше older_than.
        Если only_archived=True — удаляются только те, у кого delete_reason='archived'.
        """
        cutoff = utc_now() - older_than
        conds = [
            Message.campaign_id == self.id,
            Message.deleted_at.is_not(None),
            Message.deleted_at < cutoff,
        ]
        if only_archived:
            conds.append(Message.delete_reason == "archived")
        res = await session.execute(delete(Message).where(*conds))
        return int(res.rowcount or 0)

    # --------- Сериализация ---------
    def to_dict(self, with_messages: bool = False) -> dict[str, Any]:
        data = {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "status": self.status.value,
            "scheduled_at": self.scheduled_at.isoformat() if self.scheduled_at else None,
            "company_id": self.company_id,
            "total_messages": self.total_messages,
            "sent_count": self.sent_count,
            "delivered_count": self.delivered_count,
            "failed_count": self.failed_count,
            "deleted_at": self.deleted_at.isoformat() if self.deleted_at else None,
            "deleted_by": self.deleted_by,
            "delete_reason": self.delete_reason,
        }
        if with_messages:
            data["messages"] = [m.to_dict() for m in self.messages if not m.is_deleted]
        return data

    # --------- Фабрики для тестов ---------
    @staticmethod
    def factory(
        *,
        company_id: int,
        title: str = "Test Campaign",
        description: Optional[str] = None,
        status: CampaignStatus = CampaignStatus.DRAFT,
        scheduled_at: Optional[datetime] = None,
    ) -> Campaign:
        return Campaign(
            company_id=company_id,
            title=title,
            description=description,
            status=status,
            scheduled_at=scheduled_at,
        )

    @staticmethod
    async def create_with_messages_async(
        session,
        *,
        company_id: int,
        title: str = "Test Campaign",
        messages: int = 10,
        channel: ChannelType = ChannelType.EMAIL,
        content_template: str = "Hello, {name}!",
    ) -> Campaign:
        """
        Удобный генератор: создаёт кампанию + N сообщений (recipient=user{i}@mail).
        """
        camp = Campaign.factory(company_id=company_id, title=title)
        session.add(camp)
        await session.flush()  # чтобы camp.id появился

        bulk = []
        for i in range(messages):
            bulk.add(
                {
                    "recipient": f"user{i}@example.com",
                    "content": content_template.format(name=f"user{i}"),
                    "channel": channel,
                }
            )
        # Исправление: .add -> .append
        bulk = []
        for i in range(messages):
            bulk.append(
                {
                    "recipient": f"user{i}@example.com",
                    "content": content_template.format(name=f"user{i}"),
                    "channel": channel,
                }
            )

        await Campaign.bulk_insert_messages_async(session, camp.id, bulk)
        await camp.refresh_counters_async(session)
        return camp

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Campaign id={self.id} company_id={self.company_id} title={self.title!r} status={self.status.value} deleted={self.is_deleted}>"


# -----------------------------------------------------------------------------
# Message
# -----------------------------------------------------------------------------
class Message(SoftDeleteMixin, Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    campaign_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False, index=True
    )

    recipient: Mapped[str] = mapped_column(String(MAX_RECIPIENT_LEN), nullable=False, index=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)

    status: Mapped[MessageStatus] = mapped_column(
        SAEnum(
            MessageStatus,
            name="message_status",
            native_enum=True,
            create_constraint=False,
            validate_strings=True,
        ),
        default=MessageStatus.PENDING,
        nullable=False,
        index=True,
    )

    channel: Mapped[ChannelType] = mapped_column(
        SAEnum(
            ChannelType,
            name="message_channel",
            native_enum=True,
            create_constraint=False,
            validate_strings=True,
        ),
        default=ChannelType.EMAIL,
        nullable=False,
        index=True,
    )

    provider_message_id: Mapped[Optional[str]] = mapped_column(
        String(255), nullable=True, index=True
    )
    sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    delivered_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    error_code: Mapped[Optional[str]] = mapped_column(
        String(MAX_ERROR_CODE_LEN), nullable=True, index=True
    )
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    campaign = relationship("Campaign", back_populates="messages")

    __table_args__ = (
        CheckConstraint("length(recipient) > 0", name="ck_message_recipient_non_empty"),
        CheckConstraint("length(content) > 0", name="ck_message_content_non_empty"),
        CheckConstraint(f"length(content) <= {MAX_CONTENT_LEN}", name="ck_message_content_maxlen"),
        Index("ix_message_campaign_status_channel", "campaign_id", "status", "channel"),
    )

    # --------- Валидации ---------
    @validates("recipient")
    def _validate_recipient(self, _k: str, v: str) -> str:
        v = (v or "").strip()
        if not v:
            raise ValueError("Message.recipient must be non-empty.")
        if len(v) > MAX_RECIPIENT_LEN:
            raise ValueError(f"Message.recipient must be <= {MAX_RECIPIENT_LEN} chars.")
        return v

    @validates("content")
    def _validate_content(self, _k: str, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("Message.content must be non-empty.")
        if len(v) > MAX_CONTENT_LEN:
            raise ValueError(f"Message.content must be <= {MAX_CONTENT_LEN} chars.")
        return v

    # --------- Удобные свойства ---------
    @property
    def is_pending(self) -> bool:
        return self.status == MessageStatus.PENDING

    @property
    def is_sent(self) -> bool:
        return self.status == MessageStatus.SENT

    @property
    def is_delivered(self) -> bool:
        return self.status == MessageStatus.DELIVERED

    @property
    def is_failed(self) -> bool:
        return self.status == MessageStatus.FAILED

    @property
    def is_archived(self) -> bool:
        """
        Сообщение считается архивным, если delete_reason == 'archived'.
        Обычно также подразумевается, что оно soft-deleted.
        """
        return (self.delete_reason or "").strip().lower() == "archived"

    # --------- Бизнес-логика ---------
    def can_send(self) -> bool:
        return self.is_pending and not self.is_deleted

    def mark_sent(
        self, *, provider_id: Optional[str] = None, when: Optional[datetime] = None
    ) -> None:
        if self.is_deleted:
            raise ValueError("Нельзя изменять удалённое сообщение.")
        self.status = MessageStatus.SENT
        self.sent_at = when if when is not None else utc_now()
        if self.sent_at.tzinfo is None:
            raise ValueError("mark_sent(): 'when' must be timezone-aware (UTC).")
        if provider_id:
            self.provider_message_id = provider_id
        self.error_code = None
        self.error_message = None

    def mark_delivered(self, *, when: Optional[datetime] = None) -> None:
        if self.is_deleted:
            raise ValueError("Нельзя изменять удалённое сообщение.")
        self.status = MessageStatus.DELIVERED
        self.delivered_at = when if when is not None else utc_now()
        if self.delivered_at.tzinfo is None:
            raise ValueError("mark_delivered(): 'when' must be timezone-aware (UTC).")
        self.error_code = None
        self.error_message = None

    def mark_failed(
        self, *, reason: str, error_code: Optional[str] = None, when: Optional[datetime] = None
    ) -> None:
        if self.is_deleted:
            raise ValueError("Нельзя изменять удалённое сообщение.")
        self.status = MessageStatus.FAILED
        when = when if when is not None else utc_now()
        if when.tzinfo is None:
            raise ValueError("mark_failed(): 'when' must be timezone-aware (UTC).")
        if self.sent_at is None:
            self.sent_at = when
        self.error_code = (error_code or "").strip() or None
        self.error_message = (reason or "").strip() or "Unknown error"

    def reset_to_pending(self) -> None:
        if self.is_deleted:
            raise ValueError("Нельзя изменять удалённое сообщение.")
        self.status = MessageStatus.PENDING
        self.provider_message_id = None
        self.sent_at = None
        self.delivered_at = None
        self.error_code = None
        self.error_message = None

    def update_from_delivery_receipt(
        self,
        *,
        delivered: bool | None = None,
        error: Optional[str] = None,
        error_code: Optional[str] = None,
        delivered_at: Optional[datetime] = None,
    ) -> None:
        if delivered is True:
            self.mark_delivered(when=delivered_at)
        elif delivered is False:
            self.mark_failed(
                reason=error or "Delivery failed", error_code=error_code, when=delivered_at
            )
        elif error:
            self.mark_failed(reason=error, error_code=error_code, when=delivered_at)

    # --------- Highload batch ops ---------
    @staticmethod
    async def bulk_update_status_async(
        session,
        message_ids: Sequence[int],
        *,
        status: MessageStatus,
        when: Optional[datetime] = None,
        error_code: Optional[str] = None,
        error_message: Optional[str] = None,
    ) -> int:
        if not message_ids:
            return 0
        when = when or utc_now()
        if when.tzinfo is None:
            raise ValueError("bulk_update_status_async(): 'when' must be timezone-aware (UTC).")

        values: dict[str, Any] = {"status": status}
        if status == MessageStatus.SENT:
            values["sent_at"] = when
            values["error_code"] = None
            values["error_message"] = None
        elif status == MessageStatus.DELIVERED:
            values["delivered_at"] = when
            values["error_code"] = None
            values["error_message"] = None
        elif status == MessageStatus.FAILED:
            values["error_code"] = (error_code or "").strip() or None
            values["error_message"] = (error_message or "").strip() or "Unknown error"
            values.setdefault("sent_at", when)

        res = await session.execute(
            update(Message)
            .where(Message.id.in_(message_ids), Message.deleted_at.is_(None))
            .values(**values)
        )
        return res.rowcount or 0

    # --------- Рендер/сериализация ---------
    @staticmethod
    def render_content_static(template: str, variables: dict[str, Any]) -> str:
        class _SafeDict(dict):
            def __missing__(self, key):  # type: ignore[override]
                return "{" + key + "}"

        try:
            return template.format_map(_SafeDict(variables))
        except Exception as exc:
            raise ValueError(f"Template rendering failed: {exc}") from exc

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "campaign_id": self.campaign_id,
            "recipient": self.recipient,
            "content": self.content,
            "status": self.status.value,
            "channel": self.channel.value,
            "provider_message_id": self.provider_message_id,
            "sent_at": self.sent_at.isoformat() if self.sent_at else None,
            "delivered_at": self.delivered_at.isoformat() if self.delivered_at else None,
            "error_code": self.error_code,
            "error_message": self.error_message,
            "deleted_at": self.deleted_at.isoformat() if self.deleted_at else None,
            "deleted_by": self.deleted_by,
            "delete_reason": self.delete_reason,
        }

    # --------- Фабрики для тестов ---------
    @staticmethod
    def factory(
        *,
        campaign_id: int,
        recipient: str = "user@example.com",
        content: str = "Hello!",
        channel: ChannelType = ChannelType.EMAIL,
        status: MessageStatus = MessageStatus.PENDING,
        provider_message_id: Optional[str] = None,
        error_code: Optional[str] = None,
    ) -> Message:
        return Message(
            campaign_id=campaign_id,
            recipient=recipient,
            content=content,
            channel=channel,
            status=status,
            provider_message_id=provider_message_id,
            error_code=error_code,
        )

    @staticmethod
    async def create_batch_async(
        session,
        *,
        campaign_id: int,
        count: int = 10,
        channel: ChannelType = ChannelType.EMAIL,
        content_template: str = "Hello, {i}!",
    ) -> list[Message]:
        """
        Удобный генератор: создаёт N сообщений с recipient=user{i}@example.com.
        """
        items = []
        for i in range(count):
            items.append(
                {
                    "recipient": f"user{i}@example.com",
                    "content": content_template.format(i=i),
                    "channel": channel,
                }
            )
        await Campaign.bulk_insert_messages_async(session, campaign_id, items)
        rows = await session.execute(
            select(Message)
            .where(Message.campaign_id == campaign_id)
            .order_by(Message.id.desc())
            .limit(count)
        )
        return list(reversed([r[0] for r in rows.all()]))

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Message id={self.id} campaign_id={self.campaign_id} recipient={self.recipient!r} status={self.status.value} channel={self.channel.value} deleted={self.is_deleted}>"


# -----------------------------------------------------------------------------
# Подсказка для side связи:
# class Company(Base):
#     __tablename__ = "companies"
#     id = mapped_column(Integer, primary_key=True)
#     campaigns = relationship("Campaign", back_populates="company", cascade="all, delete-orphan")
# -----------------------------------------------------------------------------
