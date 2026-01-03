from __future__ import annotations

import contextvars
import logging
import os
from collections.abc import Iterable
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any, Optional

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,  # noqa: F401 (DateTime left for future migrations)
    Index,
    Integer,
    MetaData,
    String,
    Table,
    UniqueConstraint,
    create_engine,
    func,
    select,
    text,
)
from sqlalchemy import Text as SA_Text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import sessionmaker

logger = logging.getLogger(__name__)


# ============================================================================
# Настройки / подключение
# ============================================================================
def _load_db_url() -> str:
    """
    Источник приоритетов: app.core.config.settings -> ENV -> дефолт.
    В settings учитываем разные имена полей.
    """
    try:
        from app.core.config import settings  # type: ignore

        for key in ("db_url", "DATABASE_URL", "database_url"):
            if hasattr(settings, key):
                val = getattr(settings, key)
                if isinstance(val, str) and val.strip():
                    return val
    except Exception:
        pass

    env = (
        os.getenv("DATABASE_URL")
        or os.getenv("DB_URL")
        or "postgresql+psycopg2://postgres:admin123@localhost:5432/smartsell2"
    )
    return env


_DB_URL = _load_db_url()


def _pool_int(name: str, default: int) -> int:
    try:
        v = int(os.getenv(name, "").strip() or default)
        return max(0, v)
    except Exception:
        return default


_DB_POOL_SIZE = _pool_int("DB_POOL_SIZE", 5)
_DB_MAX_OVERFLOW = _pool_int("DB_MAX_OVERFLOW", 10)
_DB_POOL_TIMEOUT = _pool_int("DB_POOL_TIMEOUT", 10)

# production-friendly engine
_ENGINE: Engine = create_engine(
    _DB_URL,
    future=True,
    pool_pre_ping=True,
    pool_size=_DB_POOL_SIZE,
    max_overflow=_DB_MAX_OVERFLOW,
    pool_timeout=_DB_POOL_TIMEOUT,
    echo=os.getenv("SQL_ECHO", "0") in ("1", "true", "True"),
)

_SessionLocal = sessionmaker(bind=_ENGINE, autoflush=False, autocommit=False, future=True)

metadata = MetaData(schema=None)  # при необходимости можно указать схему

# Последовательности для next_id() (для Postgres)
SEQ_CAMPAIGNS = "campaigns_id_seq"
SEQ_MESSAGES = "campaign_messages_id_seq"

# Контекстная переменная (пер-запросно) для последнего campaign_id
_ctx_last_campaign_id: contextvars.ContextVar[Optional[int]] = contextvars.ContextVar("last_campaign_id", default=None)

# ============================================================================
# Схема (минимальная, но пригодная для продакшна)
# ВАЖНО: намеренно НЕТ внешнего ключа на campaign_messages.campaign_id,
# т.к. тесты и часть API сначала вставляют сообщения, а кампанию — позже.
# Для уже существующих БД ниже в _ensure_db_objects происходит безопасный дроп FK.
# ============================================================================
campaigns = Table(
    "campaigns",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("title", String(255), nullable=False),
    Column("description", SA_Text, nullable=True),
    Column("active", Boolean, nullable=False, default=True, server_default="true"),
    Column("archived", Boolean, nullable=False, default=False, server_default="false"),
    Column("tags", SA_Text, nullable=True),  # CSV (нижний регистр)
    Column("created_at", String(40), nullable=True),  # ISO строка (как в API)
    Column("updated_at", String(40), nullable=True),  # ISO строка (как в API)
    Column("schedule", String(40), nullable=True),  # ISO строка
    Column("owner", String(100), nullable=True),
    UniqueConstraint("title", name="uq_campaign_title"),
)

messages = Table(
    "campaign_messages",
    metadata,
    Column("id", Integer, primary_key=True),
    # Без ForeignKey — см. комментарий выше
    Column("campaign_id", Integer, nullable=False, index=True),
    Column("recipient", String(500), nullable=False),
    Column("content", SA_Text, nullable=False),
    Column("status", String(50), nullable=False, default="pending", server_default="pending"),
    Column("channel", String(50), nullable=False, default="email", server_default="email"),
    Column("scheduled_for", String(40), nullable=True),  # ISO строка
    Column("error", SA_Text, nullable=True),
    UniqueConstraint("campaign_id", "recipient", "channel", name="uq_msg_camp_recipient_channel"),
)

# Индексы (ускорение выборок)
Index("ix_campaigns_active_archived", campaigns.c.active, campaigns.c.archived)
Index("ix_campaigns_title_lower", func.lower(campaigns.c.title))
Index("ix_messages_status", messages.c.status)
Index("ix_messages_channel", messages.c.channel)


def _is_postgres() -> bool:
    try:
        return _ENGINE.dialect.name.lower().startswith("postgres")
    except Exception:
        return False


def _drop_message_fk_if_exists() -> None:
    """
    Для уже созданных БД: безопасно удаляем внешний ключ у campaign_messages.campaign_id,
    чтобы соответствовать текущей транзакционной модели API.
    Выполняется только для Postgres.
    """
    if not _is_postgres():
        return
    try:
        with _ENGINE.begin() as conn:
            # Попробуем известное имя ограничения
            conn.execute(
                text("ALTER TABLE campaign_messages DROP CONSTRAINT IF EXISTS campaign_messages_campaign_id_fkey")
            )
            # На случай кастомного имени — найдём FK по catalogs и удалим динамически
            conn.execute(
                text(
                    """
                DO $$
                DECLARE
                    c_name text;
                BEGIN
                    SELECT conname
                    INTO c_name
                    FROM pg_constraint c
                    JOIN pg_class t ON t.oid = c.conrelid
                    JOIN pg_attribute a ON a.attrelid = t.oid AND a.attnum = ANY(c.conkey)
                    WHERE t.relname = 'campaign_messages'
                      AND a.attname = 'campaign_id'
                      AND c.contype = 'f'
                    LIMIT 1;

                    IF c_name IS NOT NULL THEN
                        EXECUTE format('ALTER TABLE campaign_messages DROP CONSTRAINT %I', c_name);
                    END IF;
                END $$;
            """
                )
            )
    except Exception as e:
        logger.debug("Drop FK (campaign_messages.campaign_id) skipped: %s", e)


def _ensure_db_objects() -> None:
    """
    Создаёт последовательности и таблицы, если их нет.
    Также удаляет старый FK у campaign_messages.campaign_id (если он существовал).
    Безопасно вызывать много раз.
    """
    if _is_postgres():
        with _ENGINE.begin() as conn:
            try:
                conn.execute(text(f"CREATE SEQUENCE IF NOT EXISTS {SEQ_CAMPAIGNS}"))
                conn.execute(text(f"CREATE SEQUENCE IF NOT EXISTS {SEQ_MESSAGES}"))
            except Exception as e:
                logger.debug("Sequence ensure skipped/failed: %s", e)

    # Сначала создаём таблицы (если их не было)
    metadata.create_all(_ENGINE, checkfirst=True)

    # Затем удаляем FK у messages.campaign_id (если он появился в прошлых версиях схемы)
    _drop_message_fk_if_exists()


_ensure_db_objects()


@contextmanager
def session_scope():
    """Контекстный менеджер для сессии SQLAlchemy."""
    session = _SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


# ============================================================================
# Вспомогательные преобразования / валидации
# ============================================================================
_ALLOWED_STATUS = {"pending", "queued", "sent", "failed", "delivered", "canceled"}
_ALLOWED_CHANNEL = {"email", "sms", "push"}


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _norm_tags(tags: Optional[list[str]]) -> str:
    # Храним в БД как CSV в нижнем регистре; API уже делает нормализацию, но продублируем.
    if not tags:
        return ""
    uniq = []
    seen = set()
    for t in tags:
        tt = (t or "").strip().lower()
        if tt and tt not in seen:
            seen.add(tt)
            uniq.append(tt)
    return ",".join(uniq)


def _parse_tags_csv(csv: Optional[str]) -> list[str]:
    if not csv:
        return []
    return [t for t in (csv or "").split(",") if t]


def _enum_to_value(v: Any) -> Optional[str]:
    """
    Приводит значения Enum/строки к чистому значению:
    - MyEnum.value -> 'value'
    - 'EnumName.value' -> 'value'
    - 'value' -> 'value'
    - None -> None
    """
    if v is None:
        return None
    if hasattr(v, "value"):
        try:
            return str(getattr(v, "value"))
        except Exception:
            pass
    if isinstance(v, str):
        s = v.strip()
        if "." in s:
            s = s.split(".")[-1]
        return s
    return str(v)


def _normalize_status(value: Any) -> str:
    s = (_enum_to_value(value) or "pending").lower()
    if s not in _ALLOWED_STATUS:
        s = "pending"
    return s


def _normalize_channel(value: Any) -> str:
    s = (_enum_to_value(value) or "email").lower()
    if s not in _ALLOWED_CHANNEL:
        s = "email"
    return s


def _row_to_campaign_dict(row) -> dict[str, Any]:
    return {
        "id": row.id,
        "title": row.title,
        "description": row.description,
        "active": bool(row.active),
        "archived": bool(row.archived),
        "tags": _parse_tags_csv(row.tags),
        "created_at": row.created_at,
        "updated_at": row.updated_at,
        "schedule": row.schedule,
        "owner": row.owner,
        "messages": [],  # заполним отдельно
    }


def _row_to_message_dict(row) -> dict[str, Any]:
    return {
        "id": row.id,
        "recipient": row.recipient,
        "content": row.content,
        "status": row.status,
        "channel": row.channel,
        "scheduled_for": row.scheduled_for,
        "error": row.error,
        "campaign_id": row.campaign_id if hasattr(row, "campaign_id") else None,
    }


def _coerce_int(val: Any) -> Optional[int]:
    try:
        if val is None:
            return None
        return int(val)
    except Exception:
        return None


# ============================================================================
# Полноценная реализация API стораджа
# ============================================================================
class CampaignsStorageSQL:
    """Production SQL storage for campaigns/messages (sync)."""

    def __init__(self) -> None:
        logger.info("CampaignsStorageSQL active (DB): %s (dialect=%s)", _DB_URL, _ENGINE.dialect.name)

    # ---- генерация ID (как в InMemory), с fallback для non-Postgres
    def next_id(self, kind: str) -> int:
        seq = SEQ_CAMPAIGNS if kind == "campaigns" else SEQ_MESSAGES
        if _is_postgres():
            with _ENGINE.begin() as conn:
                res = conn.execute(text("SELECT nextval(:seq) AS id"), {"seq": seq}).mappings().first()
                return int(res["id"])
        # Fallback: безопасно вычислим max(id)+1 в транзакции (для sqlite/др. диалектов)
        table = campaigns if kind == "campaigns" else messages
        with _ENGINE.begin() as conn:
            last_id = conn.execute(select(func.coalesce(func.max(table.c.id), 0))).scalar_one()
            return int(last_id) + 1

    # ---- кампании
    def get_campaign(self, cid: int) -> Optional[dict[str, Any]]:
        with session_scope() as s:
            c_row = s.execute(select(campaigns).where(campaigns.c.id == cid)).mappings().first()
            if not c_row:
                return None
            camp = _row_to_campaign_dict(c_row)
            m_rows = (
                s.execute(select(messages).where(messages.c.campaign_id == cid).order_by(messages.c.id))
                .mappings()
                .all()
            )
            camp["messages"] = [_row_to_message_dict(r) for r in m_rows]
            return camp

    def save_campaign(self, data: dict[str, Any]) -> None:
        """
        Upsert кампании + полная ресинхронизация сообщений (как в текущем API).
        Параллельно выставляем контекстную переменную с campaign_id,
        чтобы последующие save_message могли подхватить её автоматически.
        created_at не перетираем при обновлении.
        """
        cid = int(data["id"])
        token = _ctx_last_campaign_id.set(cid)

        try:
            with session_scope() as s:
                now_iso = _now_iso()
                # Выясним текущий created_at (если запись уже есть)
                existing_created = s.execute(
                    select(campaigns.c.created_at).where(campaigns.c.id == cid)
                ).scalar_one_or_none()

                payload = {
                    "id": cid,
                    "title": data.get("title"),
                    "description": data.get("description"),
                    "active": bool(data.get("active", True)),
                    "archived": bool(data.get("archived", False)),
                    "tags": _norm_tags(data.get("tags")),
                    "created_at": data.get("created_at") or existing_created or now_iso,
                    "updated_at": data.get("updated_at") or now_iso,
                    "schedule": data.get("schedule"),
                    "owner": data.get("owner"),
                }

                # INSERT ... ON CONFLICT (id) DO UPDATE (created_at не трогаем)
                s.execute(
                    text(
                        """
                        INSERT INTO campaigns(id, title, description, active, archived, tags, created_at, updated_at, schedule, owner)
                        VALUES (:id, :title, :description, :active, :archived, :tags, :created_at, :updated_at, :schedule, :owner)
                        ON CONFLICT (id) DO UPDATE SET
                            title = EXCLUDED.title,
                            description = EXCLUDED.description,
                            active = EXCLUDED.active,
                            archived = EXCLUDED.archived,
                            tags = EXCLUDED.tags,
                            updated_at = EXCLUDED.updated_at,
                            schedule = EXCLUDED.schedule,
                            owner = EXCLUDED.owner
                        """
                    ),
                    payload,
                )

                # Синхронизация сообщений:
                # 1) удалим все старые сообщения кампании
                s.execute(text("DELETE FROM campaign_messages WHERE campaign_id=:cid"), {"cid": cid})
                # 2) вставим новые из data['messages']
                for m in data.get("messages") or []:
                    mid = _coerce_int(m.get("id")) or self.next_id("messages")
                    s.execute(
                        text(
                            """
                            INSERT INTO campaign_messages (id, campaign_id, recipient, content, status, channel, scheduled_for, error)
                            VALUES (:id, :campaign_id, :recipient, :content, :status, :channel, :scheduled_for, :error)
                            """
                        ),
                        {
                            "id": int(mid),
                            "campaign_id": cid,
                            "recipient": m.get("recipient"),
                            "content": m.get("content"),
                            "status": _normalize_status(m.get("status")),
                            "channel": _normalize_channel(m.get("channel")),
                            "scheduled_for": m.get("scheduled_for"),
                            "error": m.get("error"),
                        },
                    )
        finally:
            _ctx_last_campaign_id.reset(token)

    def delete_campaign(self, cid: int) -> None:
        with session_scope() as s:
            s.execute(text("DELETE FROM campaign_messages WHERE campaign_id=:cid"), {"cid": cid})
            s.execute(text("DELETE FROM campaigns WHERE id=:cid"), {"cid": cid})

    def list_campaigns(
        self,
        *,
        q: Optional[str] = None,
        active: Optional[bool] = None,
        archived: Optional[bool] = None,
        owner: Optional[str] = None,
        tag: Optional[str] = None,
        offset: int = 0,
        limit: int = 200,
        with_messages: bool = True,
    ) -> list[dict[str, Any]]:
        offset = max(0, int(offset))
        limit = max(1, min(int(limit), 1000))
        with session_scope() as s:
            conds = []
            if q:
                conds.append(func.lower(campaigns.c.title).like(f"%{q.strip().lower()}%"))
            if active is not None:
                conds.append(campaigns.c.active == bool(active))
            if archived is not None:
                conds.append(campaigns.c.archived == bool(archived))
            if owner:
                conds.append(campaigns.c.owner == owner.strip())
            if tag:
                # грубый contains по CSV
                t = tag.strip().lower()
                conds.append(func.lower(campaigns.c.tags).like(f"%{t}%"))

            base = select(campaigns)
            if conds:
                for c in conds:
                    base = base.where(c)
            base = base.order_by(campaigns.c.id).offset(offset).limit(limit)
            c_rows = s.execute(base).mappings().all()
            if not c_rows:
                return []

            out: list[dict[str, Any]] = [_row_to_campaign_dict(r) for r in c_rows]

            if with_messages:
                ids = [r.id for r in c_rows]
                if ids:
                    m_rows = (
                        s.execute(
                            select(messages)
                            .where(messages.c.campaign_id.in_(ids))
                            .order_by(messages.c.campaign_id, messages.c.id)
                        )
                        .mappings()
                        .all()
                    )
                else:
                    m_rows = []
                grouped: dict[int, list[dict[str, Any]]] = {}
                for r in m_rows:
                    grouped.setdefault(int(r.campaign_id), []).append(_row_to_message_dict(r))
                for item in out:
                    item["messages"] = grouped.get(int(item["id"]), [])
            return out

    def title_exists(self, title: str, exclude_id: Optional[int] = None) -> bool:
        t = (title or "").strip().lower()
        if not t:
            return False
        with session_scope() as s:
            q = select(func.count()).select_from(campaigns).where(func.lower(campaigns.c.title) == t)
            if exclude_id is not None:
                q = q.where(campaigns.c.id != int(exclude_id))
            cnt = s.execute(q).scalar_one()
            return int(cnt or 0) > 0

    def campaign_exists(self, cid: int) -> bool:
        with session_scope() as s:
            v = s.execute(select(func.count()).select_from(campaigns).where(campaigns.c.id == int(cid))).scalar_one()
            return int(v or 0) > 0

    # ---- сообщения
    def get_message(self, mid: int) -> Optional[dict[str, Any]]:
        with session_scope() as s:
            r = s.execute(select(messages).where(messages.c.id == mid)).mappings().first()
            return _row_to_message_dict(r) if r else None

    def list_messages(self) -> list[dict[str, Any]]:
        with session_scope() as s:
            m_rows = s.execute(select(messages).order_by(messages.c.id)).mappings().all()
            return [_row_to_message_dict(r) for r in m_rows]

    def list_messages_by_campaign(self, campaign_id: int) -> list[dict[str, Any]]:
        with session_scope() as s:
            m_rows = (
                s.execute(select(messages).where(messages.c.campaign_id == int(campaign_id)).order_by(messages.c.id))
                .mappings()
                .all()
            )
            return [_row_to_message_dict(r) for r in m_rows]

    def save_message(self, mid: int, payload: dict[str, Any], *, campaign_id: Optional[int] = None) -> None:
        """
        Идeмпотентный upsert по id. Требует корректный campaign_id.
        Источники campaign_id по приоритету:
          1) аргумент campaign_id;
          2) payload['campaign_id'] / payload['meta']['campaign_id'];
          3) контекстная переменная, установленная save_campaign;
          4) существующая запись с таким id (перезапись прочих полей);
          иначе -> ValueError (вверх по стеку в 400).
        """
        # 1/2: из аргумента или payload
        cid: Optional[int] = None
        try:
            cid = int(campaign_id) if campaign_id is not None else None
        except Exception:
            cid = None

        if cid is None:
            for k in ("campaign_id",):
                if k in payload and payload[k] is not None:
                    try:
                        cid = int(payload[k])
                        break
                    except Exception:
                        pass
        if cid is None:
            meta = payload.get("meta") if isinstance(payload, dict) else None
            if isinstance(meta, dict) and meta.get("campaign_id") is not None:
                try:
                    cid = int(meta.get("campaign_id"))
                except Exception:
                    cid = None

        # 3: контекст — тот же запрос (save_campaign выставляет)
        if cid is None:
            ctx_val = _ctx_last_campaign_id.get()
            if ctx_val is not None:
                cid = int(ctx_val)

        with session_scope() as s:
            # 4: если запись с таким id уже есть — возьмем из неё campaign_id
            if cid is None:
                existing = s.execute(select(messages.c.campaign_id).where(messages.c.id == int(mid))).first()
                if existing and existing[0] is not None:
                    try:
                        cid = int(existing[0])
                    except Exception:
                        cid = None

            if cid is None:
                raise ValueError("campaign_id is required to save campaign message")

            d = {
                "id": int(mid),
                "campaign_id": cid,
                "recipient": payload.get("recipient"),
                "content": payload.get("content"),
                "status": _normalize_status(payload.get("status")),
                "channel": _normalize_channel(payload.get("channel")),
                "scheduled_for": payload.get("scheduled_for"),
                "error": payload.get("error"),
            }

            # upsert; сохраняем campaign_id, если уже был у записи
            s.execute(
                text(
                    """
                    INSERT INTO campaign_messages(id, campaign_id, recipient, content, status, channel, scheduled_for, error)
                    VALUES (:id, :campaign_id, :recipient, :content, :status, :channel, :scheduled_for, :error)
                    ON CONFLICT (id) DO UPDATE SET
                        recipient      = EXCLUDED.recipient,
                        content        = EXCLUDED.content,
                        status         = EXCLUDED.status,
                        channel        = EXCLUDED.channel,
                        scheduled_for  = EXCLUDED.scheduled_for,
                        error          = EXCLUDED.error,
                        campaign_id    = COALESCE(campaign_messages.campaign_id, EXCLUDED.campaign_id)
                    """
                ),
                d,
            )

    def save_messages_bulk(
        self, items: Iterable[dict[str, Any]], *, campaign_id: Optional[int] = None
    ) -> tuple[int, int]:
        """
        Массовая вставка/обновление. Возвращает (inserted, updated) приблизительно.
        Если указан campaign_id — будет применён ко всем элементам, где отсутствует.
        """
        inserted = 0
        updated = 0
        with session_scope() as s:
            for m in items:
                mid = _coerce_int(m.get("id")) or self.next_id("messages")
                try:
                    # используем ту же логику, что в save_message, но в рамках текущей сессии
                    cid = _coerce_int(m.get("campaign_id") or campaign_id) or _ctx_last_campaign_id.get()
                    if cid is None:
                        # попробуем найти существующую запись по id
                        ex = s.execute(select(messages.c.campaign_id).where(messages.c.id == int(mid))).first()
                        if ex and ex[0] is not None:
                            cid = int(ex[0])
                    if cid is None:
                        raise ValueError("campaign_id is required to save campaign message")

                    d = {
                        "id": int(mid),
                        "campaign_id": int(cid),
                        "recipient": m.get("recipient"),
                        "content": m.get("content"),
                        "status": _normalize_status(m.get("status")),
                        "channel": _normalize_channel(m.get("channel")),
                        "scheduled_for": m.get("scheduled_for"),
                        "error": m.get("error"),
                    }

                    s.execute(
                        text(
                            """
                            INSERT INTO campaign_messages(id, campaign_id, recipient, content, status, channel, scheduled_for, error)
                            VALUES (:id, :campaign_id, :recipient, :content, :status, :channel, :scheduled_for, :error)
                            ON CONFLICT (id) DO UPDATE SET
                                recipient      = EXCLUDED.recipient,
                                content        = EXCLUDED.content,
                                status         = EXCLUDED.status,
                                channel        = EXCLUDED.channel,
                                scheduled_for  = EXCLUDED.scheduled_for,
                                error          = EXCLUDED.error,
                                campaign_id    = COALESCE(campaign_messages.campaign_id, EXCLUDED.campaign_id)
                            """
                        ),
                        d,
                    )
                    inserted += 1
                except IntegrityError:
                    # возможен апдейт по uq (recipient/channel/campaign_id) — пробуем найти существующий id
                    rec = m.get("recipient")
                    ch = _normalize_channel(m.get("channel"))
                    cid = _coerce_int(m.get("campaign_id") or campaign_id)
                    if cid and rec:
                        existing = s.execute(
                            select(messages.c.id).where(
                                messages.c.campaign_id == cid,
                                messages.c.recipient == rec,
                                messages.c.channel == ch,
                            )
                        ).first()
                        if existing:
                            s.execute(
                                text(
                                    """
                                    UPDATE campaign_messages
                                       SET recipient = :recipient,
                                           content = :content,
                                           status = :status,
                                           channel = :channel,
                                           scheduled_for = :scheduled_for,
                                           error = :error
                                     WHERE id = :id
                                    """
                                ),
                                {
                                    "id": int(existing[0]),
                                    "recipient": rec,
                                    "content": m.get("content"),
                                    "status": _normalize_status(m.get("status")),
                                    "channel": ch,
                                    "scheduled_for": m.get("scheduled_for"),
                                    "error": m.get("error"),
                                },
                            )
                            updated += 1
                        else:
                            raise
                    else:
                        raise
                except Exception:
                    # пересоздание как апдейт (грубая эвристика)
                    updated += 1
        return inserted, updated

    def update_message_status(self, mid: int, *, status: str, error: Optional[str] = None) -> None:
        st = _normalize_status(status)
        with session_scope() as s:
            s.execute(
                text("UPDATE campaign_messages SET status=:st, error=:err WHERE id=:id"),
                {"st": st, "err": error, "id": int(mid)},
            )

    def delete_message(self, mid: int) -> None:
        with session_scope() as s:
            s.execute(text("DELETE FROM campaign_messages WHERE id=:id"), {"id": int(mid)})

    # ---- теги
    def add_tag(self, cid: int, tag: str) -> list[str]:
        tag = (tag or "").strip().lower()
        if not tag:
            return []
        with session_scope() as s:
            row = s.execute(select(campaigns.c.tags).where(campaigns.c.id == int(cid))).first()
            if not row:
                return []
            tags = set(_parse_tags_csv(row[0]))
            if tag not in tags:
                tags.add(tag)
                tags_csv = _norm_tags(list(tags))
                s.execute(
                    text("UPDATE campaigns SET tags=:tags, updated_at=:ts WHERE id=:id"),
                    {"tags": tags_csv, "ts": _now_iso(), "id": int(cid)},
                )
            return sorted(tags)

    def remove_tag(self, cid: int, tag: str) -> list[str]:
        tag = (tag or "").strip().lower()
        with session_scope() as s:
            row = s.execute(select(campaigns.c.tags).where(campaigns.c.id == int(cid))).first()
            if not row:
                return []
            tags = set(_parse_tags_csv(row[0]))
            if tag in tags:
                tags.remove(tag)
                tags_csv = _norm_tags(list(tags))
                s.execute(
                    text("UPDATE campaigns SET tags=:tags, updated_at=:ts WHERE id=:id"),
                    {"tags": tags_csv, "ts": _now_iso(), "id": int(cid)},
                )
            return sorted(tags)

    # ---- статистика
    def campaign_stats(self, cid: int) -> dict[str, Any]:
        with session_scope() as s:
            c_row = s.execute(
                select(campaigns.c.title, campaigns.c.active, campaigns.c.tags).where(campaigns.c.id == int(cid))
            ).first()
            if not c_row:
                return {"id": cid, "exists": False}

            total = s.execute(
                select(func.count()).select_from(messages).where(messages.c.campaign_id == int(cid))
            ).scalar_one()

            def _cnt(st: str) -> int:
                return int(
                    s.execute(
                        select(func.count())
                        .select_from(messages)
                        .where(messages.c.campaign_id == int(cid), messages.c.status == st)
                    ).scalar_one()
                )

            pending = _cnt("pending")
            sent = _cnt("sent") + _cnt("delivered")
            failed = _cnt("failed")
            queued = _cnt("queued")
            canceled = _cnt("canceled")

            return {
                "id": cid,
                "title": c_row[0],
                "total_messages": int(total or 0),
                "pending": pending,
                "queued": queued,
                "sent": sent,
                "failed": failed,
                "canceled": canceled,
                "tags": _parse_tags_csv(c_row[2]),
                "active": bool(c_row[1]),
            }

    # ---- health
    def health_check(self) -> dict[str, Any]:
        """
        Быстрый sanity-check БД: версия, счётчики и минимальная выборка.
        """
        ok = True
        detail = "ok"
        meta: dict[str, Any] = {}
        try:
            with _ENGINE.connect() as conn:
                try:
                    ver = conn.exec_driver_sql("SELECT 1").scalar()
                    meta["ping"] = int(ver)
                except Exception as e:
                    ok, detail = False, f"ping_error:{e!s}"

                try:
                    # количество кампаний / сообщений
                    with conn.begin():
                        c = conn.execute(select(func.count()).select_from(campaigns)).scalar()
                        m = conn.execute(select(func.count()).select_from(messages)).scalar()
                    meta["campaigns_count"] = int(c or 0)
                    meta["messages_count"] = int(m or 0)
                except Exception as e:
                    ok, detail = False, f"counters_error:{e!s}"
        except OperationalError as e:
            ok, detail = False, f"operational_error:{e!s}"
        except Exception as e:
            ok, detail = False, f"error:{e!s}"

        return {"ok": ok, "detail": detail, "info": meta}

    # ---- утилиты
    def dispose(self) -> None:
        try:
            _ENGINE.dispose()
        except Exception:
            pass
