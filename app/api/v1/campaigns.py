# app/api/v1/campaigns.py
from __future__ import annotations

import logging
import os
import threading
import time
from datetime import UTC, datetime
from enum import Enum
from typing import Any, Literal, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Path, Query, status
from pydantic import BaseModel, Field, ValidationError, field_validator

__all__ = ["router"]

# ------------------------------------------------------------------------------
# ЛОГГИРОВАНИЕ
# ------------------------------------------------------------------------------
logger = logging.getLogger(__name__)
if not logger.handlers:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s [%(name)s] %(message)s"
    )


# ------------------------------------------------------------------------------
# ВСПОМОГАТЕЛЬНОЕ ВРЕМЯ
# ------------------------------------------------------------------------------
def _utcnow() -> datetime:
    return datetime.now(UTC)


def _now_iso() -> str:
    return _utcnow().replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse_dt(value: str) -> datetime:
    v = value.strip()
    if v.endswith("Z"):
        v = v[:-1] + "+00:00"
    dt = datetime.fromisoformat(v)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _normalize_tags(tags: list[str] | None) -> list[str]:
    norm: list[str] = []
    seen = set()
    for t in tags or []:
        tt = (t or "").strip().lower()
        if not tt:
            continue
        if tt not in seen:
            seen.add(tt)
            norm.append(tt)
    return norm


# ------------------------------------------------------------------------------
# АУДИТ (in-memory, с обрезкой)
# ------------------------------------------------------------------------------
_AUDIT: list[dict[str, Any]] = []
AUDIT_MAX_RECORDS = 10_000


def _audit(action: str, meta: dict[str, Any] | None = None) -> None:
    rec = {"ts": _now_iso(), "action": action, "meta": meta or {}}
    _AUDIT.append(rec)
    if len(_AUDIT) > AUDIT_MAX_RECORDS:
        del _AUDIT[: len(_AUDIT) - AUDIT_MAX_RECORDS]
    logger.info("audit: %s", rec)


# ------------------------------------------------------------------------------
# IN-MEMORY STORAGE (дефолт; может быть заменён на SQL ниже)
# ------------------------------------------------------------------------------
class InMemoryStorage:
    """
    Простое и потокобезопасное in-memory хранилище для кампаний и сообщений.
    Интерфейс спроектирован так, чтобы легко заменить реализацией на SQL.
    """

    def __init__(self) -> None:
        self._db: dict[str, dict[int, Any]] = {"campaigns": {}, "messages": {}}
        self._seq: dict[str, int] = {"campaigns": 0, "messages": 0}
        self._lock = threading.RLock()
        self._seed_once()

    # ---- генерация ID
    def next_id(self, kind: str) -> int:
        with self._lock:
            self._seq[kind] = (self._seq.get(kind, 0) or 0) + 1
            return self._seq[kind]

    # ---- кампании
    def get_campaign(self, cid: int) -> Optional[dict[str, Any]]:
        with self._lock:
            c = self._db["campaigns"].get(cid)
            return dict(c) if c else None

    def save_campaign(self, data: dict[str, Any]) -> None:
        with self._lock:
            data = dict(data)
            cid = int(data["id"])
            data["tags"] = _normalize_tags(data.get("tags"))
            # Сообщения в кампании — список «сырых» dict (для простоты сериализации)
            msgs = []
            for m in data.get("messages") or []:
                if isinstance(m, dict):
                    msgs.append(dict(m))
                else:
                    msgs.append(dict(getattr(m, "__dict__", {})))
            data["messages"] = msgs
            self._db["campaigns"][cid] = data

    def delete_campaign(self, cid: int) -> None:
        with self._lock:
            data = self._db["campaigns"].pop(cid, None)
            if not data:
                return
            for m in data.get("messages") or []:
                mid = int(m.get("id")) if isinstance(m, dict) else int(getattr(m, "id", 0))
                self._db["messages"].pop(mid, None)

    def list_campaigns(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(v) for v in self._db["campaigns"].values()]

    def title_exists(
        self, title: str, exclude_id: Optional[int] = None, owner: Optional[str] = None
    ) -> bool:
        """
        Проверка уникальности title среди НЕархивных кампаний.
        Опционально — в разрезе владельца (owner).
        """
        t = (title or "").strip().lower()
        own = (owner or "").strip().lower() if owner else None
        with self._lock:
            for cid, data in self._db["campaigns"].items():
                if exclude_id is not None and cid == exclude_id:
                    continue
                if data.get("archived"):
                    # архив не блокирует создание новой кампании с тем же title
                    continue
                if own is not None and (data.get("owner") or "").strip().lower() != own:
                    continue
                other = (data.get("title") or "").strip().lower()
                if t == other:
                    return True
        return False

    # ---- сообщения
    def get_message(self, mid: int) -> Optional[dict[str, Any]]:
        with self._lock:
            m = self._db["messages"].get(mid)
            return dict(m) if m else None

    def list_messages(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(v) for v in self._db["messages"].values()]

    def save_message(self, mid: int, payload: dict[str, Any]) -> None:
        with self._lock:
            self._db["messages"][mid] = dict(payload)

    def delete_message(self, mid: int) -> None:
        with self._lock:
            self._db["messages"].pop(mid, None)

    # ---- сиды (для тестов/демо)
    def _ensure_seed_campaign(self, campaign_id: int, title: str, active: bool = True) -> None:
        if campaign_id not in self._db["campaigns"]:
            self._db["campaigns"][campaign_id] = {
                "id": campaign_id,
                "title": title,
                "description": f"{title} campaign for tests",
                "active": active,
                "archived": False,
                "tags": ["test"],
                "messages": [],
                "created_at": _now_iso(),
                "updated_at": _now_iso(),
                "owner": "demo",
                "schedule": None,
            }

    def _seed_once(self) -> None:
        # по умолчанию при pytest/CI сид не включаем, чтобы не ловить коллизии title
        running_pytest = "PYTEST_CURRENT_TEST" in os.environ
        seed_enabled = os.getenv("SMARTSELL_SEED_CAMPAIGNS")
        do_seed = (seed_enabled == "1") or (seed_enabled is None and not running_pytest)
        if do_seed and not self._db["campaigns"]:
            self._db["campaigns"][1] = {
                "id": 1,
                "title": "Demo",
                "description": "Demo campaign",
                "active": True,
                "archived": False,
                "tags": ["test"],
                "messages": [],
                "created_at": _now_iso(),
                "updated_at": _now_iso(),
                "owner": "demo",
                "schedule": None,
            }
            self._db["campaigns"][999] = {
                "id": 999,
                "title": "Draft",
                "description": "Draft campaign",
                "active": False,
                "archived": False,
                "tags": ["draft"],
                "messages": [],
                "created_at": _now_iso(),
                "updated_at": _now_iso(),
                "owner": "demo",
                "schedule": None,
            }
            self._ensure_seed_campaign(1001, "Seeded")
            self._ensure_seed_campaign(1002, "Seeded 2")
            self._ensure_seed_campaign(1003, "Seeded 3")

        current_max = max(self._db["campaigns"].keys()) if self._db["campaigns"] else 0
        self._seq["campaigns"] = max(self._seq.get("campaigns", 0), current_max, 1003)


# ------------------------------------------------------------------------------
# Выбор активного STORAGE
# По умолчанию — только in-memory. SQL включается ТОЛЬКО если SMARTSELL_USE_SQL=1.
# Это избавляет от неожиданных сидов/данных в тестовой среде.
# ------------------------------------------------------------------------------
_STORAGE_BACKEND = "memory"
storage: Any

if os.getenv("SMARTSELL_USE_SQL") == "1":
    try:
        from app.storage.campaigns_sql import CampaignsStorageSQL  # type: ignore

        storage = CampaignsStorageSQL()
        _STORAGE_BACKEND = "sql"
    except Exception as _e:  # noqa: N816
        logger.info("SQL storage not available, using in-memory: %s", _e)
        storage = InMemoryStorage()
else:
    storage = InMemoryStorage()


# ------------------------------------------------------------------------------
# ВСПОМОГАТЕЛЬНЫЙ СЕЙВЕР ДЛЯ СООБЩЕНИЙ С ГАРАНТИЕЙ campaign_id
# ------------------------------------------------------------------------------
def _save_message_with_cid(mid: int, payload: dict[str, Any], campaign_id: int) -> None:
    """
    Унифицированное сохранение сообщений:
    - гарантированно проставляет campaign_id в payload;
    - совместимо с InMemoryStorage (проглотит поле);
    - совместимо с SQL-стораджем (поле NOT NULL).
    """
    data = dict(payload)
    data["campaign_id"] = int(campaign_id)
    try:
        # Новая сигнатура SQL стораджа (save_message(mid, payload, *, campaign_id=None))
        storage.save_message(mid, data, campaign_id=campaign_id)  # type: ignore[arg-type]
    except TypeError:
        # Старые/плоские реализации принимают только (mid, payload)
        storage.save_message(mid, data)  # type: ignore[arg-type]


# ------------------------------------------------------------------------------
# RATE LIMIT / DEBOUNCE (простая in-memory реализация)
# ------------------------------------------------------------------------------
class RateLimiter:
    def __init__(self) -> None:
        self._hits: dict[tuple[str, str], list[float]] = {}
        self._debounce: dict[tuple[str, str], float] = {}
        self._lock = threading.RLock()

    def check_limit(self, key: tuple[str, str], limit: int, window_sec: int) -> None:
        now = time.time()
        with self._lock:
            arr = self._hits.get(key, [])
            arr = [t for t in arr if now - t < window_sec]
            if len(arr) >= limit:
                raise HTTPException(status_code=429, detail="rate limit exceeded")
            arr.append(now)
            self._hits[key] = arr

    def check_debounce(self, key: tuple[str, str], seconds: int) -> None:
        now = time.time()
        with self._lock:
            last = self._debounce.get(key, 0.0)
            if now - last < seconds:
                raise HTTPException(status_code=429, detail="too many repeated requests")
            self._debounce[key] = now


rate_limiter = RateLimiter()


# user context (заглушка)
class UserCtx(BaseModel):
    id: int
    role: Literal["admin", "manager", "viewer"] = "manager"
    username: str = "demo"


async def get_current_user() -> UserCtx:
    # TODO: заменить на реальную вытяжку из JWT/сессии
    return UserCtx(id=1, role="manager", username="demo")


def require_role(*roles: str):
    async def dep(user: UserCtx = Depends(get_current_user)):
        if user.role not in roles:
            raise HTTPException(status_code=403, detail="forbidden")
        return user

    return dep


def _user_key(user: UserCtx) -> str:
    return f"{user.id}:{user.username}:{user.role}"


def limit_dep(bucket: str, limit: int, window: int):
    async def _dep(user: UserCtx = Depends(get_current_user)):
        rate_limiter.check_limit((bucket, _user_key(user)), limit, window)

    return _dep


def debounce_dep(bucket: str, seconds: int):
    async def _dep(user: UserCtx = Depends(get_current_user)):
        rate_limiter.check_debounce((bucket, _user_key(user)), seconds)

    return _dep


def ensure_owner_or_admin(camp_owner: Optional[str], user: UserCtx) -> None:
    if user.role != "admin":
        if (camp_owner or "").lower() != (user.username or "").lower():
            raise HTTPException(status_code=403, detail="only owner or admin")


# ------------------------------------------------------------------------------
# ОЧЕРЕДИ: Celery/RQ (с фоллбеком на inline-исполнение)
# ------------------------------------------------------------------------------
_CELERY_APP = None
_RQ_QUEUE = None


def _maybe_init_celery() -> None:
    global _CELERY_APP
    if _CELERY_APP is not None:
        return
    try:
        from celery import Celery  # type: ignore

        _CELERY_APP = Celery("smartsell")
        _CELERY_APP.conf.broker_url = (
            _CELERY_APP.conf.get("broker_url") or "redis://localhost:6379/0"
        )
        _CELERY_APP.conf.result_backend = (
            _CELERY_APP.conf.get("result_backend") or "redis://localhost:6379/0"
        )
    except Exception as e:
        logger.debug("Celery not available: %s", e)
        _CELERY_APP = None


def _maybe_init_rq() -> None:
    global _RQ_QUEUE
    if _RQ_QUEUE is not None:
        return
    try:
        import redis  # type: ignore
        from rq import Queue  # type: ignore

        conn = redis.Redis.from_url("redis://localhost:6379/0")
        _RQ_QUEUE = Queue("smartsell", connection=conn)
    except Exception as e:
        logger.debug("RQ not available: %s", e)
        _RQ_QUEUE = None


def _send_campaign_job(campaign_id: int) -> dict[str, Any]:
    data = storage.get_campaign(campaign_id)
    if not data:
        return {"status": "not_found", "campaign_id": campaign_id}

    msgs = []
    changed = 0
    for m in data.get("messages") or []:
        mm = dict(m)
        if (mm.get("status") or "").lower() in {"pending", "scheduled", "failed"}:
            mm["status"] = "sent"
            mm["error"] = None
            changed += 1
        msgs.append(mm)
    data["messages"] = msgs
    data["updated_at"] = _now_iso()
    storage.save_campaign(data)
    _audit("send_job_applied", {"id": campaign_id, "changed": changed})
    logger.info("Campaign %s sent (changed=%s)", campaign_id, changed)
    return {"status": "done", "changed": changed, "campaign_id": campaign_id}


def enqueue_send_campaign(campaign_id: int) -> dict[str, Any]:
    _maybe_init_celery()
    if _CELERY_APP:
        try:
            result = _CELERY_APP.send_task("smartsell.send_campaign", args=[campaign_id])
            _audit("enqueue_celery", {"id": campaign_id, "task_id": str(result.id)})
            logger.info("Enqueued to Celery: %s", result.id)
            return {"queued": True, "backend": "celery", "task_id": str(result.id)}
        except Exception as e:
            logger.warning("Celery enqueue failed: %s", e)

    _maybe_init_rq()
    if _RQ_QUEUE:
        try:
            job = _RQ_QUEUE.enqueue(_send_campaign_job, campaign_id)
            _audit("enqueue_rq", {"id": campaign_id, "task_id": str(job.id)})
            logger.info("Enqueued to RQ: %s", job.id)
            return {"queued": True, "backend": "rq", "task_id": str(job.id)}
        except Exception as e:
            logger.warning("RQ enqueue failed: %s", e)

    out = _send_campaign_job(campaign_id)
    out.update({"queued": False, "backend": "inline"})
    return out


# ------------------------------------------------------------------------------
# СХЕМЫ / МОДЕЛИ
# ------------------------------------------------------------------------------
class MessageStatus(str, Enum):
    pending = "pending"
    sent = "sent"
    delivered = "delivered"
    failed = "failed"
    read = "read"
    scheduled = "scheduled"
    cancelled = "cancelled"


class ChannelType(str, Enum):
    email = "email"
    whatsapp = "whatsapp"
    telegram = "telegram"
    sms = "sms"
    push = "push"


class Message(BaseModel):
    id: Optional[int] = None
    recipient: str = Field(..., min_length=1, description="Recipient (email/phone/username)")
    content: str = Field(..., min_length=1, max_length=2000)
    status: MessageStatus = MessageStatus.pending
    channel: ChannelType = ChannelType.email
    scheduled_for: Optional[str] = None  # ISO datetime (UTC)
    error: Optional[str] = None


class Campaign(BaseModel):
    id: Optional[int] = None
    title: str = Field(..., min_length=1, max_length=255)
    description: Optional[str] = None
    active: bool = True
    archived: bool = False
    tags: list[str] = Field(default_factory=list)
    messages: list[Message] = Field(default_factory=list)
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    schedule: Optional[str] = None  # ISO datetime (UTC)
    owner: Optional[str] = None

    @field_validator("tags", mode="before")
    def _ensure_tags(cls, v):
        return v or []


class CampaignStats(BaseModel):
    total_messages: int
    pending: int
    sent: int
    delivered: int
    failed: int
    read: int
    scheduled: int
    cancelled: int


class ScheduleRequest(BaseModel):
    schedule_time: str = Field(..., description="Scheduled time (ISO 8601, UTC or with TZ)")

    @field_validator("schedule_time")
    def validate_schedule_time(cls, v: str) -> str:
        try:
            dt = _parse_dt(v)
        except Exception:
            raise ValueError("schedule_time must be ISO 8601, e.g. 2025-12-31T23:59:59Z")
        if dt <= _utcnow():
            raise ValueError("schedule_time must be in the future")
        return v


class AddTagRequest(BaseModel):
    tag: str = Field(..., min_length=1, max_length=50, description="New tag")


class SetTagsRequest(BaseModel):
    tags: list[str] = Field(default_factory=list)


class PageMeta(BaseModel):
    page: int
    size: int
    total: int


class CampaignListResponse(BaseModel):
    items: list[Campaign]
    meta: PageMeta


class ArchiveRequest(BaseModel):
    reason: Optional[str] = Field(None, description="Archive reason")


class RestoreRequest(BaseModel):
    reason: Optional[str] = Field(None, description="Restore reason")


class BulkDeleteRequest(BaseModel):
    ids: list[int] = Field(default_factory=list)


class UpsertMessageRequest(BaseModel):
    recipient: str = Field(..., min_length=1)
    content: str = Field(..., min_length=1, max_length=2000)
    status: MessageStatus = MessageStatus.pending
    channel: ChannelType = ChannelType.email


class UpdateMessageStatusRequest(BaseModel):
    status: MessageStatus
    error: Optional[str] = None


class BulkStatusUpdateRequest(BaseModel):
    status: MessageStatus
    ids: list[int] = Field(default_factory=list)


class CampaignExportFormat(str, Enum):
    json = "json"
    csv = "csv"


class BulkMessageAddRequest(BaseModel):
    messages: list[UpsertMessageRequest]


class BulkUpsertMessageRequest(BaseModel):
    items: list[UpsertMessageRequest] = Field(default_factory=list)


# ------------------------------------------------------------------------------
# ХЕЛПЕРЫ ДЛЯ КАМПАНИЙ / СООБЩЕНИЙ
# ------------------------------------------------------------------------------
def _get_campaign_or_404(campaign_id: int) -> Campaign:
    data = storage.get_campaign(campaign_id)
    if not data:
        raise HTTPException(status_code=404, detail="Campaign not found")
    msgs = [Message(**m) if isinstance(m, dict) else m for m in data.get("messages") or []]
    data = {**data, "messages": [mm.model_dump() for mm in msgs]}
    return Campaign(**data)


def _save_campaign(c: Campaign) -> None:
    storage.save_campaign(c.model_dump())


def _title_exists(
    title: str, exclude_id: Optional[int] = None, owner: Optional[str] = None
) -> bool:
    """
    Обёртка над storage.title_exists:
    - учитывает owner при наличии;
    - игнорирует архивашки;
    - совместима с стораджами, где сигнатура другая.
    """
    try:
        return storage.title_exists(title, exclude_id=exclude_id, owner=owner)  # type: ignore[call-arg]
    except TypeError:
        # Фоллбек: ручной проход по списку кампаний
        t = (title or "").strip().lower()
        own = (owner or "").strip().lower() if owner else None
        for c in storage.list_campaigns():
            if exclude_id is not None and int(c.get("id")) == int(exclude_id):
                continue
            if c.get("archived"):
                continue
            if own is not None and (c.get("owner") or "").strip().lower() != own:
                continue
            if (c.get("title") or "").strip().lower() == t:
                return True
        return False


def _find_message_in_campaign(camp: Campaign, message_id: int) -> tuple[int, Optional[Message]]:
    for idx, m in enumerate(camp.messages):
        if m.id == message_id:
            return idx, m
    return -1, None


def _ensure_unique_recipient_in_campaign(
    camp: Campaign,
    recipient: str,
    channel: ChannelType,
    exclude_message_id: Optional[int] = None,
) -> None:
    r = (recipient or "").strip().lower()
    for m in camp.messages:
        if exclude_message_id is not None and m.id == exclude_message_id:
            continue
        if (m.recipient or "").lower() == r and str(m.channel) == str(channel):
            raise HTTPException(
                status_code=400,
                detail="recipient already exists in this campaign for the same channel",
            )


def _archive_campaign(campaign_id: int, reason: Optional[str] = None) -> Campaign:
    campaign = _get_campaign_or_404(campaign_id)
    campaign.active = False
    campaign.archived = True
    campaign.updated_at = _now_iso()
    _save_campaign(campaign)
    _audit("archive_campaign", {"id": campaign_id, "reason": reason})
    return campaign


def _restore_campaign(campaign_id: int, reason: Optional[str] = None) -> Campaign:
    campaign = _get_campaign_or_404(campaign_id)
    campaign.active = True
    campaign.archived = False
    campaign.updated_at = _now_iso()
    _save_campaign(campaign)
    _audit("restore_campaign", {"id": campaign_id, "reason": reason})
    return campaign


# ------------------------------------------------------------------------------
# ROUTER
# ------------------------------------------------------------------------------
router = APIRouter(prefix="/api/v1/campaigns", tags=["campaigns"])


# ---- BASE CREATE/LIST (статические пути) -------------------------------------
@router.post(
    "/",
    response_model=Campaign,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_role("admin", "manager"))],
)
async def create_campaign(
    campaign: Campaign = Body(...), user: UserCtx = Depends(get_current_user)
):
    # owner, учитываем при уникальности
    owner = (campaign.owner or user.username or "").strip()
    if _title_exists(campaign.title, owner=owner):
        raise HTTPException(status_code=400, detail="title must be unique")
    new_id = storage.next_id("campaigns")

    # подготовим payload
    payload = campaign.model_dump()
    payload["id"] = new_id
    payload["tags"] = _normalize_tags(payload.get("tags"))
    payload["created_at"] = _now_iso()
    payload["updated_at"] = _now_iso()
    payload["owner"] = owner
    payload.setdefault("archived", False)
    payload.setdefault("active", True)
    payload.setdefault("schedule", None)

    # если переданы сообщения — присвоим им ID и сохраним в message store (с campaign_id)
    msgs: list[Message] = []
    seen = set()
    for m in payload.get("messages") or []:
        msg = Message(**m) if isinstance(m, dict) else m
        key = (msg.recipient.strip().lower(), str(msg.channel))
        if key in seen:
            continue
        mid = storage.next_id("messages")
        data = msg.model_dump(exclude={"id"})
        md = Message(id=mid, **data)
        _save_message_with_cid(mid, md.model_dump(), new_id)
        msgs.append(md)
        seen.add(key)

    payload["messages"] = [mm.model_dump() for mm in msgs]

    storage.save_campaign(payload)
    _audit("create_campaign", {"id": new_id})
    logger.info("Campaign created: %s", new_id)
    return Campaign(**payload)


@router.get("/", response_model=CampaignListResponse)
async def list_campaigns(
    active: Optional[bool] = Query(None, description="Show only active"),
    archived: Optional[bool] = Query(None, description="Filter by archived flag"),
    owner: Optional[str] = Query(None, description="Filter by owner"),
    tag: Optional[str] = Query(None, description="Filter by tag (case-insensitive)"),
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    sort: Literal["created_at", "updated_at", "title"] = Query("created_at"),
    order: Literal["asc", "desc"] = Query("desc"),
):
    campaigns = [Campaign(**c) for c in storage.list_campaigns()]
    if active is not None:
        campaigns = [c for c in campaigns if c.active == active]
    if archived is not None:
        campaigns = [c for c in campaigns if c.archived == archived]
    if owner:
        own = owner.strip().lower()
        campaigns = [c for c in campaigns if (c.owner or "").lower() == own]
    if tag:
        tt = tag.strip().lower()
        campaigns = [c for c in campaigns if tt in _normalize_tags(c.tags)]
    reverse = order == "desc"
    campaigns.sort(key=lambda c: getattr(c, sort) or "", reverse=reverse)
    total = len(campaigns)
    start, end = (page - 1) * size, (page - 1) * size + size
    items = campaigns[start:end]
    return CampaignListResponse(items=items, meta=PageMeta(page=page, size=size, total=total))


# ---- Diagnostics / Search / Export / Import / Drafts (статические пути) ------
@router.get("/health")
async def campaign_health_check():
    return {
        "status": "ok",
        "storage": _STORAGE_BACKEND,
        "campaigns": len(storage.list_campaigns()),
        "messages": len(storage.list_messages()),
        "audit_records": len(_AUDIT),
        "time": _now_iso(),
    }


@router.get("/_debug/backends")
async def debug_backends():
    # Ленивая инициализация очередей (для отладки окружения)
    _maybe_init_celery()
    _maybe_init_rq()
    return {
        "storage": _STORAGE_BACKEND,
        "celery": bool(_CELERY_APP),
        "rq": bool(_RQ_QUEUE),
        "time": _now_iso(),
    }


@router.get("/_debug/audit", response_model=list[dict[str, Any]])
async def get_audit_tail(limit: int = Query(200, ge=1, le=2000)):
    return _AUDIT[-limit:]


@router.get("/search", response_model=CampaignListResponse)
async def search_campaigns(
    query: str = Query("", min_length=0, description="Search query"),
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    sort: Literal["created_at", "updated_at", "title"] = Query("created_at"),
    order: Literal["asc", "desc"] = Query("desc"),
):
    items = [Campaign(**c) for c in storage.list_campaigns()]
    if query:
        q = query.lower()
        items = [
            c
            for c in items
            if (q in (c.title or "").lower())
            or (q in (c.description or "").lower())
            or any(q in t.lower() for t in (c.tags or []))
        ]
    reverse = order == "desc"
    items.sort(key=lambda c: getattr(c, sort) or "", reverse=reverse)
    total = len(items)
    start, end = (page - 1) * size, (page - 1) * size + size
    page_items = items[start:end]
    return CampaignListResponse(items=page_items, meta=PageMeta(page=page, size=size, total=total))


@router.get("/recipients", response_model=list[str])
async def list_all_recipients():
    recs = set()
    for c in storage.list_campaigns():
        for m in c.get("messages", []) or []:
            r = m.get("recipient") if isinstance(m, dict) else getattr(m, "recipient", None)
            if r:
                recs.add((r or "").strip())
    return sorted(recs)


@router.get("/search_tags", response_model=list[str])
async def search_tags(q: str = Query("", min_length=0)):
    found = set()
    qs = (q or "").strip().lower()
    for c in storage.list_campaigns():
        for t in c.get("tags", []) or []:
            tl = (t or "").lower()
            if qs in tl:
                found.add(tl)
    return sorted(found)


@router.post("/validate", response_model=dict[str, Any])
async def validate_campaign(campaign: Campaign = Body(...)):
    try:
        Campaign(**campaign.model_dump())
    except ValidationError as e:
        return {"valid": False, "error": str(e)}
    errors: list[str] = []
    if not campaign.title or not campaign.title.strip():
        errors.append("Title is required")
    if campaign.archived:
        errors.append("Campaign is archived")
    if not campaign.messages:
        errors.append("At least one message required")
    seen = set()
    for m in campaign.messages:
        key = ((m.recipient or "").strip().lower(), str(m.channel))
        if key in seen:
            errors.append(f"duplicate recipient+channel: {m.recipient} / {m.channel}")
        seen.add(key)
    return {"valid": len(errors) == 0, "errors": errors}


@router.get("/export", response_model=list[Campaign])
async def export_campaigns():
    return [Campaign(**c) for c in storage.list_campaigns()]


@router.get("/export_format", response_model=list[str])
async def get_export_formats():
    return [f.value for f in CampaignExportFormat]


@router.get("/export/{fmt}", response_model=Any)
async def export_campaigns_fmt(fmt: CampaignExportFormat = Path(...)):
    items = [Campaign(**c) for c in storage.list_campaigns()]
    if fmt == CampaignExportFormat.json:
        return items
    elif fmt == CampaignExportFormat.csv:
        import csv
        from io import StringIO

        headers = [
            "id",
            "title",
            "description",
            "active",
            "archived",
            "tags",
            "created_at",
            "updated_at",
            "owner",
        ]
        sio = StringIO()
        writer = csv.DictWriter(sio, fieldnames=headers)
        writer.writeheader()
        for c in items:
            row = {k: getattr(c, k, "") for k in headers}
            row["tags"] = ",".join(c.tags or [])
            writer.writerow(row)
        data = sio.getvalue()
        return {"csv": data, "count": len(items)}
    else:
        raise HTTPException(status_code=400, detail="Unknown format")


@router.post(
    "/import",
    response_model=dict[str, Any],
    dependencies=[
        Depends(require_role("admin", "manager")),
        Depends(limit_dep("bulk-import", 10, 60)),
    ],
)
async def import_campaigns_bulk(
    payload: list[Campaign] = Body(...), user: UserCtx = Depends(get_current_user)
):
    imported = 0
    errors: list[dict[str, Any]] = []
    for idx, campaign in enumerate(payload):
        try:
            owner = (campaign.owner or user.username or "").strip()
            if _title_exists(campaign.title, owner=owner):
                errors.append({"index": idx, "title": campaign.title, "error": "duplicate title"})
                continue
            new_id = storage.next_id("campaigns")
            cdict = campaign.model_dump()
            cdict["id"] = new_id
            cdict["tags"] = _normalize_tags(cdict.get("tags"))
            cdict["created_at"] = _now_iso()
            cdict["updated_at"] = _now_iso()
            cdict["owner"] = owner
            cdict.setdefault("archived", False)
            cdict.setdefault("active", True)
            msgs: list[Message] = []
            seen = set()
            dup_msgs: list[dict[str, Any]] = []
            for midx, m in enumerate(cdict.get("messages") or []):
                msg = Message(**m) if isinstance(m, dict) else m
                key = (msg.recipient.strip().lower(), str(msg.channel))
                if key in seen:
                    dup_msgs.append(
                        {
                            "message_index": midx,
                            "recipient": msg.recipient,
                            "channel": str(msg.channel),
                        }
                    )
                    continue
                new_mid = storage.next_id("messages")
                md = Message(id=new_mid, **msg.model_dump())
                _save_message_with_cid(new_mid, md.model_dump(), new_id)
                msgs.append(md)
                seen.add(key)
            cdict["messages"] = [Message(**mm.model_dump()) for mm in msgs]
            storage.save_campaign({**cdict, "messages": [mm.model_dump() for mm in msgs]})
            if dup_msgs:
                errors.append(
                    {
                        "index": idx,
                        "title": cdict["title"],
                        "error": "duplicate messages",
                        "details": dup_msgs,
                    }
                )
            imported += 1
        except ValidationError as ve:
            errors.append({"index": idx, "error": "validation", "details": str(ve)})
        except Exception as e:
            errors.append({"index": idx, "error": "unknown", "details": str(e)})
    _audit("import_campaigns", {"count": imported, "errors": len(errors)})
    logger.info("Import campaigns: imported=%s errors=%s", imported, len(errors))
    return {"imported": imported, "total": len(payload), "errors": errors}


@router.post(
    "/draft",
    response_model=Campaign,
    dependencies=[Depends(require_role("admin", "manager"))],
)
async def save_campaign_draft(
    campaign: Campaign = Body(...), user: UserCtx = Depends(get_current_user)
):
    owner = (campaign.owner or user.username or "").strip()
    if _title_exists(campaign.title, owner=owner):
        raise HTTPException(status_code=400, detail="title must be unique")
    new_id = storage.next_id("campaigns")
    payload = campaign.model_dump()
    payload["id"] = new_id
    payload["active"] = False
    payload["archived"] = False
    payload["tags"] = _normalize_tags(payload.get("tags"))
    payload["created_at"] = _now_iso()
    payload["updated_at"] = _now_iso()
    payload["owner"] = owner
    payload.setdefault("schedule", None)

    # ID для сообщений, если пришли
    msgs: list[Message] = []
    seen = set()
    for m in payload.get("messages") or []:
        msg = Message(**m) if isinstance(m, dict) else m
        key = (msg.recipient.strip().lower(), str(msg.channel))
        if key in seen:
            continue
        mid = storage.next_id("messages")
        md = Message(id=mid, **msg.model_dump())
        _save_message_with_cid(mid, md.model_dump(), new_id)
        msgs.append(md)
        seen.add(key)
    payload["messages"] = [mm.model_dump() for mm in msgs]

    storage.save_campaign(payload)
    _audit("create_draft", {"id": new_id})
    logger.info("Draft created: %s", new_id)
    return Campaign(**payload)


@router.get("/drafts", response_model=CampaignListResponse)
async def list_campaign_drafts(
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    sort: Literal["created_at", "updated_at", "title"] = Query("created_at"),
    order: Literal["asc", "desc"] = Query("desc"),
):
    drafts = [Campaign(**c) for c in storage.list_campaigns() if not c.get("active", True)]
    reverse = order == "desc"
    drafts.sort(key=lambda c: getattr(c, sort) or "", reverse=reverse)
    total = len(drafts)
    start, end = (page - 1) * size, (page - 1) * size + size
    items = drafts[start:end]
    return CampaignListResponse(items=items, meta=PageMeta(page=page, size=size, total=total))


@router.get("/drafts/{campaign_id}", response_model=Campaign)
async def get_campaign_draft(campaign_id: int = Path(..., ge=1)):
    data = storage.get_campaign(campaign_id)
    if not data or data.get("active", True):
        raise HTTPException(status_code=404, detail="Draft not found")
    return _get_campaign_or_404(campaign_id)


# ---- DYNAMIC PATHS (/{campaign_id}...) ---------------------------------------
@router.get("/{campaign_id}", response_model=Campaign)
async def get_campaign(campaign_id: int = Path(..., ge=1)):
    return _get_campaign_or_404(campaign_id)


@router.put(
    "/{campaign_id}",
    response_model=Campaign,
    dependencies=[Depends(require_role("admin", "manager"))],
)
async def update_campaign(
    campaign_id: int, campaign: Campaign = Body(...), user: UserCtx = Depends(get_current_user)
):
    current = storage.get_campaign(campaign_id)
    if not current:
        raise HTTPException(status_code=404, detail="Campaign not found")
    ensure_owner_or_admin(current.get("owner"), user)
    # проверим уникальность названия, если пришло новое
    want_change_title = ("title" in campaign.model_fields_set) or (
        campaign.title and (campaign.title != current.get("title"))
    )
    owner = (campaign.owner or current.get("owner") or user.username or "").strip()
    if want_change_title and _title_exists(campaign.title, exclude_id=campaign_id, owner=owner):
        raise HTTPException(status_code=400, detail="title must be unique")
    updated = {**current, **campaign.model_dump(exclude_unset=True)}
    updated["id"] = campaign_id
    updated["tags"] = _normalize_tags(updated.get("tags"))
    updated["updated_at"] = _now_iso()
    updated["owner"] = owner or current.get("owner")

    # сообщения: если в payload пришли — синхронизируем стор
    if "messages" in campaign.model_fields_set:
        # очистим все старые message ids из стора и создадим заново
        old_msgs: list[dict[str, Any]] = current.get("messages") or []
        for m in old_msgs:
            mid = int(m.get("id")) if isinstance(m, dict) else int(getattr(m, "id", 0))
            if mid:
                storage.delete_message(mid)
        msgs_out: list[dict[str, Any]] = []
        seen = set()
        for m in updated.get("messages") or []:
            msg = Message(**m) if isinstance(m, dict) else m
            key = (msg.recipient.strip().lower(), str(msg.channel))
            if key in seen:
                continue
            new_mid = storage.next_id("messages")
            md = Message(id=new_mid, **msg.model_dump())
            _save_message_with_cid(new_mid, md.model_dump(), campaign_id)
            msgs_out.append(md.model_dump())
            seen.add(key)
        updated["messages"] = msgs_out

    storage.save_campaign(updated)
    _audit("update_campaign", {"id": campaign_id})
    logger.info("Campaign updated: %s", campaign_id)
    return _get_campaign_or_404(campaign_id)


@router.delete(
    "/{campaign_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_role("admin", "manager"))],
)
async def delete_campaign(campaign_id: int, user: UserCtx = Depends(get_current_user)):
    data = storage.get_campaign(campaign_id)
    if not data:
        return
    ensure_owner_or_admin(data.get("owner"), user)
    storage.delete_campaign(campaign_id)
    _audit("delete_campaign", {"id": campaign_id})
    logger.info("Campaign deleted: %s", campaign_id)
    return


# ---- Messages (CRUD, status, bulk)
def _message_recipient(m: Any) -> Optional[str]:
    if isinstance(m, dict):
        return m.get("recipient")
    return getattr(m, "recipient", None)


@router.post(
    "/{campaign_id}/messages",
    response_model=Message,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_role("admin", "manager"))],
)
async def add_message_to_campaign(
    campaign_id: int, message: Message = Body(...), user: UserCtx = Depends(get_current_user)
):
    camp = _get_campaign_or_404(campaign_id)
    ensure_owner_or_admin(camp.owner, user)
    _ensure_unique_recipient_in_campaign(camp, message.recipient, message.channel)
    new_id = storage.next_id("messages")
    payload = message.model_dump()
    payload["id"] = new_id
    _save_message_with_cid(new_id, payload, campaign_id)
    camp.messages.append(Message(**payload))
    camp.updated_at = _now_iso()
    _save_campaign(camp)
    _audit("add_message", {"campaign_id": campaign_id, "message_id": new_id})
    logger.info("Message added: campaign=%s message=%s", campaign_id, new_id)
    return Message(**payload)


@router.get("/{campaign_id}/messages", response_model=list[Message])
async def list_campaign_messages(campaign_id: int):
    camp = _get_campaign_or_404(campaign_id)
    return camp.messages


@router.get("/{campaign_id}/messages/{message_id}", response_model=Message)
async def get_campaign_message(campaign_id: int, message_id: int):
    camp = _get_campaign_or_404(campaign_id)
    _, found = _find_message_in_campaign(camp, message_id)
    if not found:
        raise HTTPException(status_code=404, detail="Message not found")
    return found


@router.put(
    "/{campaign_id}/messages/{message_id}",
    response_model=Message,
    dependencies=[Depends(require_role("admin", "manager"))],
)
async def update_campaign_message(
    campaign_id: int,
    message_id: int,
    message: Message = Body(...),
    user: UserCtx = Depends(get_current_user),
):
    camp = _get_campaign_or_404(campaign_id)
    ensure_owner_or_admin(camp.owner, user)
    idx, found = _find_message_in_campaign(camp, message_id)
    if not found:
        raise HTTPException(status_code=404, detail="Message not found")
    rec = message.recipient if "recipient" in message.model_fields_set else found.recipient
    ch = message.channel if "channel" in message.model_fields_set else found.channel
    _ensure_unique_recipient_in_campaign(camp, rec, ch, exclude_message_id=message_id)
    current = storage.get_message(message_id)
    if not current:
        raise HTTPException(status_code=404, detail="Message not found")
    updated = {**current, **message.model_dump(exclude_unset=True), "id": message_id}
    _save_message_with_cid(message_id, updated, campaign_id)
    camp.messages[idx] = Message(**updated)
    camp.updated_at = _now_iso()
    _save_campaign(camp)
    _audit("update_message", {"campaign_id": campaign_id, "message_id": message_id})
    logger.info("Message updated: campaign=%s message=%s", campaign_id, message_id)
    return Message(**updated)


@router.delete(
    "/{campaign_id}/messages/{message_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_role("admin", "manager"))],
)
async def delete_campaign_message(
    campaign_id: int, message_id: int, user: UserCtx = Depends(get_current_user)
):
    camp = _get_campaign_or_404(campaign_id)
    ensure_owner_or_admin(camp.owner, user)
    storage.delete_message(message_id)
    camp.messages = [m for m in camp.messages if m.id != message_id]
    camp.updated_at = _now_iso()
    _save_campaign(camp)
    _audit("delete_message", {"campaign_id": campaign_id, "message_id": message_id})
    logger.info("Message deleted: campaign=%s message=%s", campaign_id, message_id)
    return


@router.post(
    "/{campaign_id}/messages/upsert_by_recipient",
    response_model=Message,
    dependencies=[Depends(require_role("admin", "manager"))],
)
async def upsert_message_by_recipient(
    campaign_id: int, req: UpsertMessageRequest, user: UserCtx = Depends(get_current_user)
):
    camp = _get_campaign_or_404(campaign_id)
    ensure_owner_or_admin(camp.owner, user)
    for m in camp.messages:
        if m.recipient.strip().lower() == req.recipient.strip().lower() and str(m.channel) == str(
            req.channel
        ):
            m.content = req.content
            m.status = req.status
            camp.updated_at = _now_iso()
            _save_campaign(camp)
            _save_message_with_cid(int(m.id), m.model_dump(), campaign_id)
            _audit("upsert_message_update", {"campaign_id": campaign_id, "message_id": m.id})
            logger.info("Message upsert(update): campaign=%s message=%s", campaign_id, m.id)
            return m
    new_id = storage.next_id("messages")
    msg = Message(id=new_id, **req.model_dump())
    camp.messages.append(msg)
    camp.updated_at = _now_iso()
    _save_campaign(camp)
    _save_message_with_cid(new_id, msg.model_dump(), campaign_id)
    _audit("upsert_message_insert", {"campaign_id": campaign_id, "message_id": new_id})
    logger.info("Message upsert(insert): campaign=%s message=%s", campaign_id, new_id)
    return msg


@router.post(
    "/{campaign_id}/messages/{message_id}/status",
    response_model=Message,
    dependencies=[Depends(require_role("admin", "manager"))],
)
async def set_message_status(
    campaign_id: int,
    message_id: int,
    payload: UpdateMessageStatusRequest,
    user: UserCtx = Depends(get_current_user),
):
    camp = _get_campaign_or_404(campaign_id)
    ensure_owner_or_admin(camp.owner, user)
    idx, msg = _find_message_in_campaign(camp, message_id)
    if not msg:
        raise HTTPException(status_code=404, detail="Message not found")
    msg.status = payload.status
    msg.error = payload.error
    camp.messages[idx] = msg
    camp.updated_at = _now_iso()
    _save_campaign(camp)
    _save_message_with_cid(message_id, msg.model_dump(), campaign_id)
    _audit(
        "set_message_status",
        {"campaign_id": campaign_id, "message_id": message_id, "status": str(payload.status)},
    )
    logger.info(
        "Message status set: campaign=%s message=%s status=%s",
        campaign_id,
        message_id,
        payload.status,
    )
    return msg


@router.post(
    "/{campaign_id}/messages/{message_id}/reset_to_pending",
    response_model=Message,
    dependencies=[Depends(require_role("admin", "manager"))],
)
async def reset_message_to_pending(
    campaign_id: int, message_id: int, user: UserCtx = Depends(get_current_user)
):
    camp = _get_campaign_or_404(campaign_id)
    ensure_owner_or_admin(camp.owner, user)
    idx, msg = _find_message_in_campaign(camp, message_id)
    if not msg:
        raise HTTPException(status_code=404, detail="Message not found")
    msg.status = MessageStatus.pending
    msg.error = None
    camp.messages[idx] = msg
    camp.updated_at = _now_iso()
    _save_campaign(camp)
    _save_message_with_cid(message_id, msg.model_dump(), campaign_id)
    _audit("reset_to_pending", {"campaign_id": campaign_id, "message_id": message_id})
    logger.info("Message reset to pending: campaign=%s message=%s", campaign_id, message_id)
    return msg


@router.post(
    "/{campaign_id}/messages/clear_failed",
    response_model=dict[str, int],
    dependencies=[
        Depends(require_role("admin", "manager")),
        Depends(limit_dep("clear-failed", 30, 60)),
    ],
)
async def clear_failed_messages(campaign_id: int, user: UserCtx = Depends(get_current_user)):
    camp = _get_campaign_or_404(campaign_id)
    ensure_owner_or_admin(camp.owner, user)
    before = len(camp.messages)
    to_delete = [m.id for m in camp.messages if m.status == MessageStatus.failed]
    camp.messages = [m for m in camp.messages if m.status != MessageStatus.failed]
    for mid in to_delete:
        storage.delete_message(int(mid))
    if to_delete:
        camp.updated_at = _now_iso()
        _save_campaign(camp)
    _audit("clear_failed_messages", {"campaign_id": campaign_id, "removed": len(to_delete)})
    logger.info("Failed messages cleared: campaign=%s removed=%s", campaign_id, len(to_delete))
    return {"removed": len(to_delete), "remaining": len(camp.messages), "before": before}


@router.post(
    "/{campaign_id}/messages/mark_all_sent",
    response_model=dict[str, int],
    dependencies=[
        Depends(require_role("admin", "manager")),
        Depends(limit_dep("mark-sent", 60, 60)),
    ],
)
async def mark_all_sent(campaign_id: int, user: UserCtx = Depends(get_current_user)):
    camp = _get_campaign_or_404(campaign_id)
    ensure_owner_or_admin(camp.owner, user)
    changed = 0
    for i, m in enumerate(camp.messages):
        if m.status in (MessageStatus.pending, MessageStatus.failed, MessageStatus.scheduled):
            m.status = MessageStatus.sent
            m.error = None
            camp.messages[i] = m
            _save_message_with_cid(int(m.id), m.model_dump(), campaign_id)
            changed += 1
    if changed:
        camp.updated_at = _now_iso()
        _save_campaign(camp)
    _audit("mark_all_sent", {"campaign_id": campaign_id, "changed": changed})
    logger.info("All marked sent: campaign=%s changed=%s", campaign_id, changed)
    return {"changed": changed, "total": len(camp.messages)}


@router.post(
    "/{campaign_id}/messages/bulk_status_update",
    response_model=dict[str, Any],
    dependencies=[
        Depends(require_role("admin", "manager")),
        Depends(limit_dep("bulk-status", 60, 60)),
    ],
)
async def bulk_update_message_status(
    campaign_id: int, req: BulkStatusUpdateRequest, user: UserCtx = Depends(get_current_user)
):
    camp = _get_campaign_or_404(campaign_id)
    ensure_owner_or_admin(camp.owner, user)
    updated = 0
    for i, m in enumerate(camp.messages):
        if m.id in req.ids:
            m.status = req.status
            camp.messages[i] = m
            _save_message_with_cid(int(m.id), m.model_dump(), campaign_id)
            updated += 1
    if updated:
        camp.updated_at = _now_iso()
        _save_campaign(camp)
    _audit("bulk_update_message_status", {"campaign_id": campaign_id, "updated": updated})
    logger.info("Bulk status update: campaign=%s updated=%s", campaign_id, updated)
    return {"updated": updated, "requested": len(req.ids)}


@router.post(
    "/{campaign_id}/messages/bulk_delete",
    response_model=dict[str, Any],
    dependencies=[
        Depends(require_role("admin", "manager")),
        Depends(limit_dep("bulk-delete-msg", 30, 60)),
    ],
)
async def bulk_delete_messages(
    campaign_id: int, req: BulkDeleteRequest, user: UserCtx = Depends(get_current_user)
):
    camp = _get_campaign_or_404(campaign_id)
    ensure_owner_or_admin(camp.owner, user)
    before = len(camp.messages)
    to_delete = set(req.ids)
    camp.messages = [m for m in camp.messages if m.id not in to_delete]
    for mid in to_delete:
        storage.delete_message(int(mid))
    if to_delete:
        camp.updated_at = _now_iso()
        _save_campaign(camp)
    _audit("bulk_delete_messages", {"campaign_id": campaign_id, "deleted": len(to_delete)})
    logger.info("Bulk delete messages: campaign=%s deleted=%s", campaign_id, len(to_delete))
    return {"deleted": len(to_delete), "remaining": len(camp.messages), "before": before}


@router.post(
    "/{campaign_id}/messages/bulk_add",
    response_model=dict[str, Any],
    dependencies=[
        Depends(require_role("admin", "manager")),
        Depends(limit_dep("bulk-add-msg", 30, 60)),
    ],
)
async def bulk_add_messages(
    campaign_id: int, req: BulkMessageAddRequest, user: UserCtx = Depends(get_current_user)
):
    camp = _get_campaign_or_404(campaign_id)
    ensure_owner_or_admin(camp.owner, user)
    added = 0
    dup_errors: list[dict[str, Any]] = []
    existing = {(m.recipient.strip().lower(), str(m.channel)) for m in camp.messages}
    for idx, msg_req in enumerate(req.messages):
        key = (msg_req.recipient.strip().lower(), str(msg_req.channel))
        if key in existing:
            dup_errors.append(
                {
                    "index": idx,
                    "recipient": msg_req.recipient,
                    "channel": msg_req.channel,
                    "error": "duplicate recipient+channel",
                }
            )
            continue
        new_id = storage.next_id("messages")
        msg = Message(id=new_id, **msg_req.model_dump())
        camp.messages.append(msg)
        _save_message_with_cid(new_id, msg.model_dump(), campaign_id)
        existing.add(key)
        added += 1
    camp.updated_at = _now_iso()
    _save_campaign(camp)
    _audit(
        "bulk_add_messages",
        {"campaign_id": campaign_id, "added": added, "duplicates": len(dup_errors)},
    )
    logger.info(
        "Bulk add messages: campaign=%s added=%s duplicates=%s", campaign_id, added, len(dup_errors)
    )
    return {"added": added, "total": len(camp.messages), "errors": dup_errors}


@router.post(
    "/{campaign_id}/messages/bulk_upsert",
    response_model=dict[str, Any],
    dependencies=[
        Depends(require_role("admin", "manager")),
        Depends(limit_dep("bulk-upsert-msg", 30, 60)),
    ],
)
async def bulk_upsert_messages(
    campaign_id: int, req: BulkUpsertMessageRequest, user: UserCtx = Depends(get_current_user)
):
    camp = _get_campaign_or_404(campaign_id)
    ensure_owner_or_admin(camp.owner, user)

    updated = 0
    inserted = 0
    index_by_key = {
        (m.recipient.strip().lower(), str(m.channel)): i for i, m in enumerate(camp.messages)
    }

    for item in req.items:
        key = (item.recipient.strip().lower(), str(item.channel))
        if key in index_by_key:
            i = index_by_key[key]
            m = camp.messages[i]
            m.content = item.content
            m.status = item.status
            _save_message_with_cid(int(m.id), m.model_dump(), campaign_id)
            updated += 1
        else:
            new_id = storage.next_id("messages")
            msg = Message(id=new_id, **item.model_dump())
            camp.messages.append(msg)
            _save_message_with_cid(new_id, msg.model_dump(), campaign_id)
            index_by_key[key] = len(camp.messages) - 1
            inserted += 1

    if updated or inserted:
        camp.updated_at = _now_iso()
        _save_campaign(camp)

    _audit(
        "bulk_upsert_messages",
        {"campaign_id": campaign_id, "updated": updated, "inserted": inserted},
    )
    logger.info(
        "Bulk upsert messages: campaign=%s updated=%s inserted=%s",
        campaign_id,
        updated,
        inserted,
    )
    return {"updated": updated, "inserted": inserted, "total": len(camp.messages)}


# ---- Actions / Stats / Schedules ---------------------------------------------
@router.get("/{campaign_id}/stats", response_model=CampaignStats)
async def get_campaign_stats(campaign_id: int):
    camp = _get_campaign_or_404(campaign_id)
    total = len(camp.messages)

    def cnt(st: MessageStatus) -> int:
        return sum(1 for m in camp.messages if m.status == st)

    stats = CampaignStats(
        total_messages=total,
        pending=cnt(MessageStatus.pending),
        sent=cnt(MessageStatus.sent),
        delivered=cnt(MessageStatus.delivered),
        failed=cnt(MessageStatus.failed),
        read=cnt(MessageStatus.read),
        scheduled=cnt(MessageStatus.scheduled),
        cancelled=cnt(MessageStatus.cancelled),
    )
    return stats


@router.post(
    "/{campaign_id}/send",
    response_model=dict[str, Any],
    dependencies=[Depends(require_role("admin", "manager")), Depends(debounce_dep("send", 3))],
)
async def send_campaign(campaign_id: int, user: UserCtx = Depends(get_current_user)):
    camp = _get_campaign_or_404(campaign_id)
    ensure_owner_or_admin(camp.owner, user)
    _audit("send_campaign", {"id": campaign_id})
    logger.info("Send requested (sync-like): %s", campaign_id)
    result = enqueue_send_campaign(campaign_id)
    return {"status": "started", "campaign_id": campaign_id, **result}


@router.post(
    "/{campaign_id}/send_async",
    response_model=dict[str, Any],
    dependencies=[
        Depends(require_role("admin", "manager")),
        Depends(debounce_dep("send-async", 3)),
    ],
)
async def send_campaign_async(campaign_id: int, user: UserCtx = Depends(get_current_user)):
    camp = _get_campaign_or_404(campaign_id)
    ensure_owner_or_admin(camp.owner, user)
    out = enqueue_send_campaign(campaign_id)
    _audit(
        "send_campaign_async",
        {"id": campaign_id, **{k: out[k] for k in out if k in ("queued", "backend", "task_id")}},
    )
    logger.info("Send async requested: %s -> %s", campaign_id, out)
    return {"campaign_id": campaign_id, **out}


@router.post(
    "/{campaign_id}/schedule",
    response_model=dict[str, Any],
    dependencies=[Depends(require_role("admin", "manager")), Depends(debounce_dep("schedule", 3))],
)
async def schedule_campaign(
    campaign_id: int,
    schedule: ScheduleRequest = Body(...),
    user: UserCtx = Depends(get_current_user),
):
    camp = _get_campaign_or_404(campaign_id)
    ensure_owner_or_admin(camp.owner, user)
    camp.schedule = schedule.schedule_time
    changed = 0
    for i, m in enumerate(camp.messages):
        if m.status == MessageStatus.pending:
            m.status = MessageStatus.scheduled
            camp.messages[i] = m
            _save_message_with_cid(int(m.id), m.model_dump(), campaign_id)
            changed += 1
    camp.updated_at = _now_iso()
    _save_campaign(camp)
    _audit(
        "schedule_campaign", {"id": campaign_id, "time": schedule.schedule_time, "changed": changed}
    )
    logger.info(
        "Campaign scheduled: %s at %s (changed=%s)", campaign_id, schedule.schedule_time, changed
    )
    return {
        "status": "scheduled",
        "campaign_id": campaign_id,
        "time": schedule.schedule_time,
        "changed": changed,
    }


@router.post(
    "/{campaign_id}/cancel_schedule",
    response_model=Campaign,
    dependencies=[
        Depends(require_role("admin", "manager")),
        Depends(debounce_dep("schedule-cancel", 3)),
    ],
)
async def cancel_campaign_schedule(campaign_id: int, user: UserCtx = Depends(get_current_user)):
    camp = _get_campaign_or_404(campaign_id)
    ensure_owner_or_admin(camp.owner, user)
    camp.schedule = None
    camp.updated_at = _now_iso()
    _save_campaign(camp)
    _audit("cancel_schedule", {"id": campaign_id})
    logger.info("Campaign schedule canceled: %s", campaign_id)
    return camp


@router.post("/{campaign_id}/preview_send", response_model=dict[str, Any])
async def preview_send(campaign_id: int, recipients: list[str] = Body(default_factory=list)):
    camp = _get_campaign_or_404(campaign_id)
    recs = [r for r in recipients if isinstance(r, str) and r.strip()]
    if not recs:
        raise HTTPException(status_code=400, detail="No recipients provided")
    return {
        "status": "preview",
        "campaign_id": campaign_id,
        "recipients": recs,
        "messages": min(len(camp.messages), len(recs)),
    }


@router.post(
    "/{campaign_id}/resend_failed",
    response_model=dict[str, Any],
    dependencies=[
        Depends(require_role("admin", "manager")),
        Depends(limit_dep("resend-failed", 30, 60)),
    ],
)
async def resend_failed(campaign_id: int, user: UserCtx = Depends(get_current_user)):
    camp = _get_campaign_or_404(campaign_id)
    ensure_owner_or_admin(camp.owner, user)
    count = 0
    for i, m in enumerate(camp.messages):
        if m.status == MessageStatus.failed:
            m.status = MessageStatus.pending
            m.error = None
            camp.messages[i] = m
            _save_message_with_cid(int(m.id), m.model_dump(), campaign_id)
            count += 1
    if count:
        camp.updated_at = _now_iso()
        _save_campaign(camp)
    _audit("resend_failed", {"id": campaign_id, "count": count})
    logger.info("Resend failed queued: campaign=%s count=%s", campaign_id, count)
    return {"status": "queued", "count": count}


# ---- Tags & Archive/Restore ---------------------------------------------------
@router.get("/{campaign_id}/tags", response_model=list[str])
async def get_campaign_tags(campaign_id: int):
    camp = _get_campaign_or_404(campaign_id)
    return _normalize_tags(camp.tags)


@router.post(
    "/{campaign_id}/tags",
    response_model=Campaign,
    dependencies=[Depends(require_role("admin", "manager"))],
)
async def add_tag_to_campaign(
    campaign_id: int, tag_req: AddTagRequest = Body(...), user: UserCtx = Depends(get_current_user)
):
    campaign = _get_campaign_or_404(campaign_id)
    ensure_owner_or_admin(campaign.owner, user)
    new_tag = _normalize_tags([tag_req.tag])
    campaign.tags = _normalize_tags((campaign.tags or []) + new_tag)
    campaign.updated_at = _now_iso()
    _save_campaign(campaign)
    _audit("add_tag", {"id": campaign_id, "tag": new_tag})
    logger.info("Tag added: campaign=%s tag=%s", campaign_id, new_tag)
    return campaign


@router.delete(
    "/{campaign_id}/tags/{tag}",
    response_model=Campaign,
    dependencies=[Depends(require_role("admin", "manager"))],
)
async def remove_tag_from_campaign(
    campaign_id: int, tag: str = Path(..., min_length=1), user: UserCtx = Depends(get_current_user)
):
    campaign = _get_campaign_or_404(campaign_id)
    ensure_owner_or_admin(campaign.owner, user)
    tt = (tag or "").strip().lower()
    campaign.tags = [t for t in _normalize_tags(campaign.tags) if t != tt]
    campaign.updated_at = _now_iso()
    _save_campaign(campaign)
    _audit("remove_tag", {"id": campaign_id, "tag": tt})
    logger.info("Tag removed: campaign=%s tag=%s", campaign_id, tt)
    return campaign


@router.put(
    "/{campaign_id}/tags",
    response_model=Campaign,
    dependencies=[Depends(require_role("admin", "manager"))],
)
async def set_tags_for_campaign(
    campaign_id: int, req: SetTagsRequest, user: UserCtx = Depends(get_current_user)
):
    campaign = _get_campaign_or_404(campaign_id)
    ensure_owner_or_admin(campaign.owner, user)
    campaign.tags = _normalize_tags(req.tags)
    campaign.updated_at = _now_iso()
    _save_campaign(campaign)
    _audit("set_tags", {"id": campaign_id, "tags": campaign.tags})
    logger.info("Tags set: campaign=%s tags=%s", campaign_id, campaign.tags)
    return campaign


@router.post(
    "/{campaign_id}/archive",
    response_model=Campaign,
    dependencies=[Depends(require_role("admin", "manager"))],
)
async def archive_campaign(
    campaign_id: int, req: ArchiveRequest = Body(None), user: UserCtx = Depends(get_current_user)
):
    data = storage.get_campaign(campaign_id)
    if not data:
        raise HTTPException(status_code=404, detail="Campaign not found")
    ensure_owner_or_admin(data.get("owner"), user)
    return _archive_campaign(campaign_id, reason=req.reason if req else None)


@router.post(
    "/{campaign_id}/restore",
    response_model=Campaign,
    dependencies=[Depends(require_role("admin", "manager"))],
)
async def restore_campaign(
    campaign_id: int, req: RestoreRequest = Body(None), user: UserCtx = Depends(get_current_user)
):
    data = storage.get_campaign(campaign_id)
    if not data:
        raise HTTPException(status_code=404, detail="Campaign not found")
    ensure_owner_or_admin(data.get("owner"), user)
    return _restore_campaign(campaign_id, reason=req.reason if req else None)


# ---- Bulk archive/restore/delete ---------------------------------------------
@router.post(
    "/bulk_archive",
    response_model=dict[str, Any],
    dependencies=[Depends(require_role("admin", "manager"))],
)
async def bulk_archive_campaigns(req: BulkDeleteRequest, user: UserCtx = Depends(get_current_user)):
    archived = 0
    for cid in req.ids:
        data = storage.get_campaign(cid)
        if not data:
            continue
        ensure_owner_or_admin(data.get("owner"), user)
        _archive_campaign(cid)
        archived += 1
    logger.info("Bulk archive: archived=%s requested=%s", archived, len(req.ids))
    return {"archived": archived, "requested": len(req.ids)}


@router.post(
    "/bulk_restore",
    response_model=dict[str, Any],
    dependencies=[Depends(require_role("admin", "manager"))],
)
async def bulk_restore_campaigns(req: BulkDeleteRequest, user: UserCtx = Depends(get_current_user)):
    restored = 0
    for cid in req.ids:
        data = storage.get_campaign(cid)
        if not data:
            continue
        ensure_owner_or_admin(data.get("owner"), user)
        _restore_campaign(cid)
        restored += 1
    logger.info("Bulk restore: restored=%s requested=%s", restored, len(req.ids))
    return {"restored": restored, "requested": len(req.ids)}


@router.post(
    "/bulk_delete",
    response_model=dict[str, Any],
    dependencies=[Depends(require_role("admin", "manager"))],
)
async def bulk_delete_campaigns(req: BulkDeleteRequest, user: UserCtx = Depends(get_current_user)):
    deleted = 0
    for cid in req.ids:
        data = storage.get_campaign(cid)
        if not data:
            continue
        ensure_owner_or_admin(data.get("owner"), user)
        storage.delete_campaign(cid)
        deleted += 1
    _audit("bulk_delete_campaigns", {"deleted": deleted})
    logger.info("Bulk delete campaigns: deleted=%s requested=%s", deleted, len(req.ids))
    return {"deleted": deleted, "requested": len(req.ids)}


# ---- Analytics / Diagnostics / Errors ----------------------------------------
@router.get("/{campaign_id}/advanced_stats", response_model=dict[str, Any])
async def advanced_campaign_stats(campaign_id: int):
    camp = _get_campaign_or_404(campaign_id)
    msg_by_channel: dict[str, list[Message]] = {}
    for m in camp.messages:
        key = str(m.channel)
        msg_by_channel.setdefault(key, []).append(m)
    return {
        "total_messages": len(camp.messages),
        "by_channel": {k: len(v) for k, v in msg_by_channel.items()},
        "unique_recipients": len(set((m.recipient or "").strip().lower() for m in camp.messages)),
        "tags": _normalize_tags(camp.tags),
        "active": camp.active,
        "archived": camp.archived,
        "scheduled_for": camp.schedule,
    }


@router.get("/error/test")
async def error_test_example():
    raise HTTPException(status_code=418, detail="I'm a teapot")


@router.get("/error/raise/{code}")
async def raise_custom_error(code: int = Path(..., ge=400, le=599)):
    raise HTTPException(status_code=code, detail=f"Custom error: {code}")
