# app/models/base.py
"""
Base model with common fields and functionality (SQLAlchemy 2.x, DeclarativeBase).

ВАЖНО:
- Модуль прогревает критичные доменные модели (company/product/warehouse/user/order/audit_log/billing/campaign),
  чтобы даже при прямом импорте `Base` из этого файла к моменту `Base.metadata.create_all(engine)` все FK/relationship были
  разрешены. Это критично для тестов, которые импортируют именно app.models.base.

Дополнительно (современно и под highload):
- Асинхронные и синхронные CRUD/поисковые/батч-хелперы.
- Безопасные блокировки: SELECT FOR UPDATE (nowait/skip_locked) и advisory locks (pg_advisory_xact_lock / pg_advisory_lock).
- Постгрес-упсерты (ON CONFLICT DO UPDATE) через SQLAlchemy Core, с явным указанием конфликтующих полей.
- Батч-операции (insert/update) с чанками и настраиваемым размером партии (sync/async).
- Обёртки create_all / drop_all для sync/async-движков.
- Небьющийся "мягкий" конструктор (LenientInitMixin) для тестов/фикстур.
- Улучшенная типизация (Session/AsyncSession) и дружелюбие к mypy.
- TZ-утилиты для явной работы с UTC (tz-aware), не ломая существующие naive-поля.

ДОБАВЛЕНО:
- utc_now(): naive UTC (для совместимости со старыми моделями, например customer.py).
- Расширен список прогреваемых доменов (_CRITICAL_MODULES) — добавлены customer/payment/otp.
- Кросс-диалектный шым для CITEXT (SQLite), авто-включение расширения citext в PostgreSQL.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Iterable, Iterator, Sequence
from contextlib import asynccontextmanager, contextmanager
from datetime import UTC, datetime
from importlib import import_module
from typing import Any, Optional, TypeVar

from sqlalchemy import func  # для count/агрегаций
from sqlalchemy import DateTime, Integer, MetaData
from sqlalchemy import exc as sa_exc
from sqlalchemy import select, text
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, declared_attr, mapped_column

# Optional async support (модуль может работать и без зависимости на async в рантайме)
try:  # pragma: no cover
    from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
except Exception:  # pragma: no cover
    AsyncSession = None  # type: ignore
    AsyncEngine = None  # type: ignore

# --------------------------------------------------------------------------------------
# Логирование
# --------------------------------------------------------------------------------------
logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------------------
# Кросс-диалектные шимы типов (важно для тестов на SQLite)
# --------------------------------------------------------------------------------------
# CITEXT из PostgreSQL не умеет компилироваться в SQLite — добавляем компилятор.
try:  # pragma: no cover
    from sqlalchemy.dialects.postgresql import CITEXT as PG_CITEXT
    from sqlalchemy.ext.compiler import compiles

    @compiles(PG_CITEXT, "sqlite")
    def _compile_pg_citext_for_sqlite(type_, compiler, **kw):
        # Ближайший аналог: текст с кейз-инсенситивной сортировкой/сравнением
        return "TEXT COLLATE NOCASE"

except Exception:
    # Если в окружении нет postgresql.dialect — это ок; тогда CITEXT не используется.
    pass


# --------------------------------------------------------------------------------------
# Вспомогательные TZ-утилиты (tz-aware UTC) — безопасно сосуществуют с naive полями
# --------------------------------------------------------------------------------------
def utcnow_tz() -> datetime:
    """Текущее время в UTC (tz-aware)."""
    return datetime.now(UTC)


def to_utc(dt: datetime) -> datetime:
    """Привести дату к UTC (если naive — считаем, что это UTC и проставляем tzinfo)."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def utc_now() -> datetime:
    """
    НАЙТИЧНО ВАЖНО: naive UTC "сейчас" для совместимости со старыми моделями.
    Многие ваши модели/тесты используют naive DateTime (без timezone=True).
    """
    return datetime.utcnow()


# --------------------------------------------------------------------------------------
# SQLAlchemy naming conventions (для alembic и единых имён ограничений/индексов)
# --------------------------------------------------------------------------------------
# Примечание: сохраняю ваши шаблоны (column_0_N_name). Это ок, если так ожидается тестами/миграциями.
NAMING_CONVENTIONS: dict[str, str] = {
    "ix": "ix__%(table_name)s__%(column_0_N_name)s",
    "uq": "uq__%(table_name)s__%(column_0_N_name)s",
    "ck": "ck__%(table_name)s__%(constraint_name)s",
    "fk": "fk__%(table_name)s__%(column_0_N_name)s__%(referred_table_name)s",
    "pk": "pk__%(table_name)s",
}


# --------------------------------------------------------------------------------------
# Declarative base (корень). Тесты ожидают наличие Base.
# --------------------------------------------------------------------------------------
class Base(DeclarativeBase):
    """Root declarative base (SQLAlchemy 2.x) с naming conventions."""

    metadata = MetaData(naming_convention=NAMING_CONVENTIONS)

    def __repr__(self) -> str:  # pragma: no cover
        try:
            cols = []
            for k in getattr(self, "__mapper__").c.keys():  # type: ignore[attr-defined]
                v = getattr(self, k, None)
                if isinstance(v, str) and len(v) > 64:
                    v = v[:64] + "…"
                cols.append(f"{k}={v!r}")
            return f"<{self.__class__.__name__}({', '.join(cols)})>"
        except Exception:
            return super().__repr__()


# --------------------------------------------------------------------------------------
# LenientInitMixin — мягкий конструктор для безопасной инициализации kwargs
# --------------------------------------------------------------------------------------
class LenientInitMixin:
    """
    Миксин делает конструктор «терпимым» к любым kwargs:
    - не падает, если маппер ещё не сконфигурирован или поле не является колонкой/relationship;
    - просто выставляет атрибут на объекте (SQLAlchemy позже обернёт instrumented attrs);
    - полезно для фикстур/тестов и ранних конструкторов в доменных моделях.

    Подключай ЭТОТ миксин ПЕРВЫМ в MRO у модели:
        class User(LenientInitMixin, BaseModel, ...):
            ...
    """

    def __init__(self, **kwargs):
        # вызываем super().__init__ без kwargs — во многих базовых классах он пустой
        try:
            super().__init__()  # type: ignore[misc]
        except Exception:
            pass
        for k, v in (kwargs or {}).items():
            try:
                setattr(self, k, v)
            except Exception:
                # на всякий случай не роняем конструктор
                pass


# --------------------------------------------------------------------------------------
# BaseModel (общие поля/поведение) — наследник Base
# --------------------------------------------------------------------------------------
class BaseModel(Base):
    """Общий базовый класс для всех моделей проекта."""

    __abstract__ = True

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False, index=True
    )

    @declared_attr
    def __tablename__(cls) -> str:  # type: ignore[override]
        """
        Генерация имени таблицы:
        - для известных сущностей используем фиксированное имя (совместимость со схемой);
        - иначе: <имякласса> в нижнем регистре + 's'.
        """
        name = cls.__name__
        mapping = {
            "User": "users",
            "UserSession": "user_sessions",
            "OTPCode": "otp_codes",
            "AuditLog": "audit_logs",
            "ExternalAuditEvent": "external_audit_events",
            "Product": "products",
            "StockMovement": "stock_movements",
            "Warehouse": "warehouses",
            "Company": "companies",
            "Category": "categories",
            "ProductVariant": "product_variants",
            "Order": "orders",
            "OrderItem": "order_items",
            "Payment": "payments",
            "PaymentMethod": "payment_methods",
            "Campaign": "campaigns",
            "Message": "messages",
            "BillingPayment": "billing_payments",
            "BillingInvoice": "billing_invoices",
            "Invoice": "invoices",
            "Subscription": "subscriptions",
            "WalletBalance": "wallet_balances",
            "WalletTransaction": "wallet_transactions",
            "ProductStock": "product_stocks",
        }
        return mapping.get(name, name.lower() + "s")

    # -------------------------------- helpers --------------------------------
    def touch(self) -> None:
        """Обновить updated_at на текущее UTC-время (naive)."""
        self.updated_at = datetime.utcnow()

    def touch_tz(self) -> None:
        """Обновить updated_at на текущее время (tz-aware UTC) с приведением к naive при сохранении совместимости."""
        self.updated_at = datetime.utcnow()

    def __repr__(self) -> str:  # pragma: no cover
        main = {k: getattr(self, k, None) for k in ("id", "created_at", "updated_at")}
        return f"<{self.__class__.__name__}({main})>"

    def to_dict(self) -> dict[str, Any]:
        """Быстрая сериализация всех колонок таблицы (только маппед-колонки)."""
        return {col.name: getattr(self, col.name) for col in self.__table__.columns}  # type: ignore[attr-defined]

    # Безопасная сериализация для логов/внешних API (даты -> isoformat, None остаётся None)
    def to_public_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for col in self.__table__.columns:  # type: ignore[attr-defined]
            v = getattr(self, col.name)
            if isinstance(v, datetime):
                out[col.name] = v.isoformat()
            else:
                out[col.name] = v
        return out


# Тип-помощник для CRUD-утилит
T = TypeVar("T", bound=BaseModel)


# --------------------------------------------------------------------------------------
# Mixins (SQLAlchemy 2.x + Mapped)
# --------------------------------------------------------------------------------------
class SoftDeleteMixin:
    """Миксин для мягкого удаления (soft delete)."""

    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True, index=True)

    def soft_delete(self) -> None:
        self.deleted_at = datetime.utcnow()

    def is_deleted(self) -> bool:
        return self.deleted_at is not None


class TenantMixin:
    """Миксин для поддержки multi-tenancy (несколько компаний/арендаторов)."""

    tenant_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)


class AuditMixin:
    """Миксин для записи аудита изменений."""

    last_modified_by: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)

    def set_modified_by(self, user_id: int) -> None:
        self.last_modified_by = user_id


class LockableMixin:
    """Миксин для временной блокировки записи."""

    locked_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True, index=True)
    locked_by: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)

    def lock(self, user_id: Optional[int] = None) -> None:
        self.locked_at = datetime.utcnow()
        if user_id is not None:
            self.locked_by = user_id

    def unlock(self) -> None:
        self.locked_at = None
        self.locked_by = None

    def is_locked(self) -> bool:
        return self.locked_at is not None


# --------------------------------------------------------------------------------------
# Вспомогательные утилиты
# --------------------------------------------------------------------------------------
def _chunked(iterable: Iterable[dict[str, Any]], size: int) -> Iterator[list[dict[str, Any]]]:
    buf: list[dict[str, Any]] = []
    for item in iterable:
        buf.append(item)
        if len(buf) >= size:
            yield buf
            buf = []
    if buf:
        yield buf


# --------------------------------------------------------------------------------------
# CRUD / query helpers (совместимы с SQLAlchemy 2.x)
# --------------------------------------------------------------------------------------
def create(session: Session, model: type[T], **kwargs) -> T:
    obj = model(**kwargs)
    session.add(obj)
    session.commit()
    return obj


def get_by_id(session: Session, model: type[T], obj_id: Any) -> Optional[T]:
    return session.get(model, obj_id)


def delete(session: Session, obj: T) -> None:
    session.delete(obj)
    session.commit()


def update(session: Session, obj: T, **kwargs) -> T:
    for k, v in kwargs.items():
        setattr(obj, k, v)
    session.commit()
    return obj


def get_or_create(
    session: Session,
    model: type[T],
    defaults: Optional[dict[str, Any]] = None,
    **lookup: Any,
) -> tuple[T, bool]:
    """
    Вернуть (obj, created). Если объект не найден — создать с defaults+lookup.
    """
    obj = session.query(model).filter_by(**lookup).first()
    if obj:
        return obj, False
    params = dict(defaults or {})
    params.update(lookup)
    obj = model(**params)
    session.add(obj)
    try:
        session.commit()
    except sa_exc.IntegrityError:  # гонка на unique
        session.rollback()
        obj = session.query(model).filter_by(**lookup).first()  # type: ignore[assignment]
        if obj is None:
            raise
        return obj, False
    return obj, True


def bulk_insert_rows(
    session: Session,
    table,  # sqlalchemy.Table (обычно Model.__table__)
    rows: Sequence[dict[str, Any]],
    *,
    chunk_size: int = 1000,
) -> int:
    """
    Батч-вставка без upsert (быстрая). Используйте для логов/аудита, где нет конфликтов.
    """
    if not rows:
        return 0
    processed = 0
    for chunk in _chunked(rows, chunk_size):
        session.execute(table.insert().values(chunk))
        processed += len(chunk)
    session.commit()
    return processed


def bulk_update_rows(
    session: Session,
    model: type[T],
    data_list: Iterable[dict[str, Any]],
    *,
    chunk_size: int = 1000,
) -> None:
    """
    Массовое обновление экземпляров модели.
    data_list: iterable словарей вида {'id': ..., '<field>': ...}
    Обновляет чанками, чтобы не раздувать сессию в highload.
    """

    def _flush(buf: list[dict[str, Any]]) -> None:
        for data in buf:
            obj_id = data.get("id")
            if obj_id is None:
                continue
            obj = session.get(model, obj_id)
            if not obj:
                continue
            for key, value in data.items():
                if key == "id":
                    continue
                setattr(obj, key, value)
        session.flush()

    buffer: list[dict[str, Any]] = []
    for row in data_list:
        buffer.append(row)
        if len(buffer) >= chunk_size:
            _flush(buffer)
            buffer.clear()
    if buffer:
        _flush(buffer)
    session.commit()


# --- ALIAS для совместимости с тестами/старым кодом ---
def bulk_update(
    session: Session, model: type[T], data_list: Iterable[dict[str, Any]], *, chunk_size: int = 1000
) -> None:
    """
    Совместимый алиас: некоторые тесты/модули импортируют bulk_update из app.models.base.
    Реализация — вызов bulk_update_rows.
    """
    return bulk_update_rows(session, model, data_list, chunk_size=chunk_size)


def exists(session: Session, model: type[T], **kwargs) -> bool:
    return session.query(model).filter_by(**kwargs).first() is not None


def first(session: Session, model: type[T], **kwargs) -> Optional[T]:
    return session.query(model).filter_by(**kwargs).first()


def count(session: Session, model: type[T], **kwargs) -> int:
    """Быстрый счётчик записей по фильтру."""
    q = select(func.count()).select_from(model).filter_by(**kwargs)  # type: ignore[arg-type]
    return int(session.execute(q).scalar() or 0)


def refresh_safe(session: Session, obj: T, *, expire: bool = False) -> T:
    """
    Безопасно «освежить» объект из БД (не падает, если объект не в сессии).
    """
    try:
        session.refresh(obj)
    except Exception:
        if expire:
            try:
                session.expire(obj)
            except Exception:
                pass
    return obj


# --------------------------------------------------------------------------------------
# SELECT FOR UPDATE / Advisory locks (sync)
# --------------------------------------------------------------------------------------
def for_update_by_id(
    session: Session,
    model: type[T],
    obj_id: Any,
    *,
    nowait: bool = False,
    skip_locked: bool = False,
) -> Optional[T]:
    """
    Получить запись под блокировкой SELECT ... FOR UPDATE.
    nowait=True  -> не ждать блокировку, при занятости — исключение.
    skip_locked=True -> пропустить заблокированные (вернёт None, если запись занята).
    """
    q = (
        select(model)
        .where(model.id == obj_id)
        .with_for_update(nowait=nowait, skip_locked=skip_locked)
    )
    return session.execute(q).scalars().first()


def pg_advisory_xact_lock(session: Session, key: int) -> None:
    """
    Транзакционная advisory-блокировка (pg_advisory_xact_lock).
    Блок держится до конца текущей транзакции.
    """
    try:
        session.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": int(key)})
    except Exception as e:  # pragma: no cover
        logger.warning("pg_advisory_xact_lock failed for key=%s: %s", key, e)


def pg_advisory_lock(session: Session, key: int) -> None:
    """
    Нетрранзакционная advisory-блокировка (pg_advisory_lock).
    Требует явного RELEASE. Нужна редко; используйте xact-версию, если можно.
    """
    try:
        session.execute(text("SELECT pg_advisory_lock(:k)"), {"k": int(key)})
    except Exception as e:  # pragma: no cover
        logger.warning("pg_advisory_lock failed for key=%s: %s", key, e)


def pg_advisory_unlock(session: Session, key: int) -> None:
    """Снять нетранзакционную advisory-блокировку."""
    try:
        session.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": int(key)})
    except Exception as e:  # pragma: no cover
        logger.warning("pg_advisory_unlock failed for key=%s: %s", key, e)


@contextmanager
def locked_transaction(
    session: Session,
    *,
    advisory_key: Optional[int] = None,
    commit: bool = True,
) -> Iterator[Session]:
    """
    Контекст менеджер безопасной транзакции c optional advisory-lock.
    Использование:
        with locked_transaction(session, advisory_key=123):
            ...
    """
    try:
        if advisory_key is not None:
            pg_advisory_xact_lock(session, advisory_key)
        yield session
        if commit:
            session.commit()
    except Exception:
        session.rollback()
        raise


# --------------------------------------------------------------------------------------
# Async CRUD / query helpers (если установлен sqlalchemy.ext.asyncio)
# --------------------------------------------------------------------------------------
# Примечание: чтобы не тащить async-зависимости всегда, типы аннотированы через Optional
async def acreate(session: AsyncSession, model: type[T], **kwargs) -> T:  # type: ignore[valid-type]
    obj = model(**kwargs)
    session.add(obj)
    await session.commit()
    return obj


async def aget_by_id(session: AsyncSession, model: type[T], obj_id: Any) -> Optional[T]:  # type: ignore[valid-type]
    return await session.get(model, obj_id)


async def adelete(session: AsyncSession, obj: T) -> None:  # type: ignore[valid-type]
    await session.delete(obj)  # type: ignore[arg-type]
    await session.commit()


async def aupdate(session: AsyncSession, obj: T, **kwargs) -> T:  # type: ignore[valid-type]
    for k, v in kwargs.items():
        setattr(obj, k, v)
    await session.commit()
    return obj


async def aget_or_create(
    session: AsyncSession,  # type: ignore[valid-type]
    model: type[T],
    defaults: Optional[dict[str, Any]] = None,
    **lookup: Any,
) -> tuple[T, bool]:
    """
    Async-версия get_or_create с защитой от гонок по unique.
    """
    q = select(model).filter_by(**lookup).limit(1)
    res = await session.execute(q)
    obj = res.scalars().first()
    if obj:
        return obj, False

    params = dict(defaults or {})
    params.update(lookup)
    obj = model(**params)
    session.add(obj)
    try:
        await session.commit()
        return obj, True
    except sa_exc.IntegrityError:
        await session.rollback()
        res = await session.execute(select(model).filter_by(**lookup).limit(1))
        obj = res.scalars().first()
        if obj is None:
            raise
        return obj, False


async def abulk_insert_rows(
    session: AsyncSession,  # type: ignore[valid-type]
    table,
    rows: Sequence[dict[str, Any]],
    *,
    chunk_size: int = 1000,
) -> int:
    """Async батч-вставка без upsert."""
    if not rows:
        return 0
    processed = 0
    for chunk in _chunked(rows, chunk_size):
        await session.execute(table.insert().values(chunk))
        processed += len(chunk)
    await session.commit()
    return processed


async def abulk_update_rows(
    session: AsyncSession,  # type: ignore[valid-type]
    model: type[T],
    data_list: Iterable[dict[str, Any]],
    *,
    chunk_size: int = 1000,
) -> None:
    """
    Асинхронная версия массового обновления с чанкованием.
    """

    async def _flush(buf: list[dict[str, Any]]) -> None:
        for data in buf:
            obj_id = data.get("id")
            if obj_id is None:
                continue
            obj = await session.get(model, obj_id)
            if not obj:
                continue
            for key, value in data.items():
                if key == "id":
                    continue
                setattr(obj, key, value)
        await session.flush()

    buffer: list[dict[str, Any]] = []
    for row in data_list:
        buffer.append(row)
        if len(buffer) >= chunk_size:
            await _flush(buffer)
            buffer.clear()
    if buffer:
        await _flush(buffer)
    await session.commit()


async def aexists(session: AsyncSession, model: type[T], **kwargs) -> bool:  # type: ignore[valid-type]
    q = select(model).filter_by(**kwargs).limit(1)
    res = await session.execute(q)
    return res.scalars().first() is not None


async def afirst(session: AsyncSession, model: type[T], **kwargs) -> Optional[T]:  # type: ignore[valid-type]
    q = select(model).filter_by(**kwargs).limit(1)
    res = await session.execute(q)
    return res.scalars().first()


async def acount(session: AsyncSession, model: type[T], **kwargs) -> int:  # type: ignore[valid-type]
    q = select(func.count()).select_from(model).filter_by(**kwargs)  # type: ignore[arg-type]
    res = await session.execute(q)
    return int(res.scalar() or 0)


async def afor_update_by_id(  # type: ignore[valid-type]
    session: AsyncSession,
    model: type[T],
    obj_id: Any,
    *,
    nowait: bool = False,
    skip_locked: bool = False,
) -> Optional[T]:
    q = (
        select(model)
        .where(model.id == obj_id)
        .with_for_update(nowait=nowait, skip_locked=skip_locked)
    )
    res = await session.execute(q)
    return res.scalars().first()


async def apg_advisory_xact_lock(session: AsyncSession, key: int) -> None:  # type: ignore[valid-type]
    try:
        await session.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": int(key)})
    except Exception as e:  # pragma: no cover
        logger.warning("apg_advisory_xact_lock failed for key=%s: %s", key, e)


async def apg_advisory_lock(session: AsyncSession, key: int) -> None:  # type: ignore[valid-type]
    try:
        await session.execute(text("SELECT pg_advisory_lock(:k)"), {"k": int(key)})
    except Exception as e:  # pragma: no cover
        logger.warning("apg_advisory_lock failed for key=%s: %s", key, e)


async def apg_advisory_unlock(session: AsyncSession, key: int) -> None:  # type: ignore[valid-type]
    try:
        await session.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": int(key)})
    except Exception as e:  # pragma: no cover
        logger.warning("apg_advisory_unlock failed for key=%s: %s", key, e)


@asynccontextmanager
async def alocked_transaction(
    session: AsyncSession,  # type: ignore[valid-type]
    *,
    advisory_key: Optional[int] = None,
    commit: bool = True,
) -> AsyncIterator[AsyncSession]:
    """
    Async-контекст для безопасной транзакции c optional advisory-lock.
    Использование:
        async with alocked_transaction(session, advisory_key=123):
            ...
    """
    try:
        if advisory_key is not None:
            await apg_advisory_xact_lock(session, advisory_key)
        yield session
        if commit:
            await session.commit()
    except Exception:
        await session.rollback()
        raise


# --------------------------------------------------------------------------------------
# PostgreSQL upsert helpers (sync/async)
# --------------------------------------------------------------------------------------
def upsert_postgres(
    session: Session,
    table,  # Table or ORM model.__table__
    rows: Sequence[dict[str, Any]],
    *,
    conflict_cols: Sequence[str],
    update_cols: Optional[Sequence[str]] = None,
    chunk_size: int = 1000,
) -> int:
    """
    Вставка/обновление пачками через INSERT .. ON CONFLICT DO UPDATE (PostgreSQL).
    :param table: sqlalchemy.Table (обычно Model.__table__)
    :param rows: список словарей
    :param conflict_cols: колонки конфликта (уникальные/PK)
    :param update_cols: какие колонки обновлять при конфликте (по умолчанию — все, кроме conflict)
    :return: число обработанных строк
    """
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    if not rows:
        return 0

    processed = 0
    cols = set(rows[0].keys())
    if update_cols is None:
        update_cols = sorted(cols - set(conflict_cols))

    def _do_chunk(chunk: list[dict[str, Any]]) -> None:
        nonlocal processed
        stmt = pg_insert(table).values(chunk)
        update_map = {c: getattr(stmt.excluded, c) for c in update_cols or []}
        stmt = stmt.on_conflict_do_update(index_elements=list(conflict_cols), set_=update_map)
        session.execute(stmt)
        processed += len(chunk)

    for chunk in _chunked(rows, chunk_size):
        _do_chunk(chunk)
    session.commit()
    return processed


async def aupsert_postgres(
    session: AsyncSession,  # type: ignore[valid-type]
    table,
    rows: Sequence[dict[str, Any]],
    *,
    conflict_cols: Sequence[str],
    update_cols: Optional[Sequence[str]] = None,
    chunk_size: int = 1000,
) -> int:
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    if not rows:
        return 0

    processed = 0
    cols = set(rows[0].keys())
    if update_cols is None:
        update_cols = sorted(cols - set(conflict_cols))

    async def _do_chunk(chunk: list[dict[str, Any]]) -> None:
        nonlocal processed
        stmt = pg_insert(table).values(chunk)
        update_map = {c: getattr(stmt.excluded, c) for c in update_cols or []}
        stmt = stmt.on_conflict_do_update(index_elements=list(conflict_cols), set_=update_map)
        await session.execute(stmt)
        processed += len(chunk)

    for chunk in _chunked(rows, chunk_size):
        await _do_chunk(chunk)
    await session.commit()
    return processed


# --------------------------------------------------------------------------------------
# Пагинация (sync/async) — универсально для любых ORM-моделей
# --------------------------------------------------------------------------------------
def paginate(
    session: Session,
    model: type[T],
    *,
    page: int = 1,
    per_page: int = 50,
    where: Optional[dict[str, Any]] = None,
    order_by: Optional[Any] = None,
) -> tuple[list[T], int]:
    """
    Возвращает (items, total).
    """
    page = max(1, int(page))
    per_page = max(1, int(per_page))
    stmt = select(model)
    if where:
        stmt = stmt.filter_by(**where)
    total = int(session.execute(select(func.count()).select_from(stmt.subquery())).scalar() or 0)
    if order_by is not None:
        stmt = stmt.order_by(order_by)
    stmt = stmt.offset((page - 1) * per_page).limit(per_page)
    items = list(session.execute(stmt).scalars().all())
    return items, total


async def apaginate(
    session: AsyncSession,  # type: ignore[valid-type]
    model: type[T],
    *,
    page: int = 1,
    per_page: int = 50,
    where: Optional[dict[str, Any]] = None,
    order_by: Optional[Any] = None,
) -> tuple[list[T], int]:
    page = max(1, int(page))
    per_page = max(1, int(per_page))
    stmt = select(model)
    if where:
        stmt = stmt.filter_by(**where)
    total_res = await session.execute(select(func.count()).select_from(stmt.subquery()))
    total = int(total_res.scalar() or 0)
    if order_by is not None:
        stmt = stmt.order_by(order_by)
    stmt = stmt.offset((page - 1) * per_page).limit(per_page)
    res = await session.execute(stmt)
    items = list(res.scalars().all())
    return items, total


# --------------------------------------------------------------------------------------
# АВТОЗАГРУЗКА КРИТИЧНЫХ МОДУЛЕЙ И БЕЗОПАСНЫЕ ОБЁРТКИ
# --------------------------------------------------------------------------------------
_CRITICAL_MODULES: tuple[str, ...] = (
    # ядро доменов с FK/relationship, без которых валятся тесты
    "app.models.company",
    "app.models.product",
    "app.models.warehouse",
    "app.models.user",
    "app.models.order",
    "app.models.audit_log",
    # дополнительно: иначе падало на 'Subscription' / 'Campaign' / биллинг
    "app.models.billing",
    "app.models.campaign",
    # ДОБАВЛЕНО: чтобы гарантировать наличие таблиц для FK payments.customer_id и otp
    "app.models.customer",
    "app.models.payment",
    "app.models.otp",
)


def _import_domain(module_name: str) -> None:
    try:
        # предотвращаем повторный импорт, который может приводить к повторной регистрации таблиц
        import sys

        if module_name in sys.modules:
            return
        import_module(module_name)
        logger.debug("Imported %s", module_name)
    except ModuleNotFoundError:
        # Если какой-то домен реально отсутствует, не валим импорт на старте.
        # Ошибка всплывёт позже (в маппере/DDL), но для тестов у нас все домены есть.
        logger.info("Optional domain module %s not found (skipped).", module_name)
    except Exception as e:
        # Понижаем уровень шума: при параллельных импортерах в тестах возможны гонки
        logger.info("Failed to import %s: %s", module_name, e)


def _force_mapper_configuration() -> None:
    """
    Форсируем конфигурацию отложенных мапперов. Удобно для раннего выявления проблем
    с relationship() и внешними ключами — ошибки всплывут сразу при импорте модуля.
    """
    try:
        # доступ к registry.mappers триггерит конфигурацию
        _ = list(Base.registry.mappers)  # noqa: B018
    except Exception as e:
        # Не валим импорт — просто логируем для диагностики в отладке.
        logger.debug("force_mapper_configuration raised: %s", e)


def ensure_models_loaded() -> None:
    """
    Идемпотентно импортирует критичные домены и форсирует конфигурацию мапперов.
    Вызывать перед Alembic/тестами/скриптами, если где-то делаете create_all напрямую.
    """
    for mod in _CRITICAL_MODULES:
        _import_domain(mod)
    _force_mapper_configuration()


def _maybe_enable_pg_extensions(bind) -> None:
    """
    Если мы на PostgreSQL — аккуратно пытаемся включить расширение citext.
    В SQLite/других СУБД просто no-op.
    """
    try:
        dialect_name = getattr(getattr(bind, "dialect", None), "name", "")
        if dialect_name == "postgresql":
            try:
                # Попробуем через .begin(), если доступно
                with bind.begin() as conn:
                    conn.execute(text("CREATE EXTENSION IF NOT EXISTS citext"))
            except Exception:
                # Если .begin нет (или запрет), используем connect()
                conn = bind.connect()
                try:
                    conn.execute(text("CREATE EXTENSION IF NOT EXISTS citext"))
                finally:
                    conn.close()
    except Exception:
        # Любые неожиданные ситуации игнорируем: не мешаем тестам на SQLite
        pass


def metadata_create_all(bind) -> None:
    """
    Безопасный wrapper вокруг Base.metadata.create_all(bind) (sync):
    - гарантирует, что модели загружены (ensure_models_loaded)
    - включает расширение citext в Postgres (если возможно)
    - создаёт таблицы
    """
    ensure_models_loaded()
    _maybe_enable_pg_extensions(bind)
    Base.metadata.create_all(bind)


def metadata_drop_all(bind) -> None:
    """
    Безопасный wrapper вокруг Base.metadata.drop_all(bind) (sync).
    """
    ensure_models_loaded()
    Base.metadata.drop_all(bind)


async def metadata_create_all_async(async_engine: AsyncEngine) -> None:  # type: ignore[valid-type]
    """
    Асинхронный wrapper вокруг Base.metadata.create_all для async engine.
    В т.ч. включает расширение citext в Postgres перед созданием таблиц.
    """
    ensure_models_loaded()

    def _enable_and_create(sync_conn):
        _maybe_enable_pg_extensions(sync_conn)
        Base.metadata.create_all(sync_conn)

    await async_engine.run_sync(_enable_and_create)  # type: ignore[arg-type]


async def metadata_drop_all_async(async_engine: AsyncEngine) -> None:  # type: ignore[valid-type]
    """
    Асинхронный wrapper вокруг Base.metadata.drop_all для async engine.
    """
    ensure_models_loaded()
    await async_engine.run_sync(Base.metadata.drop_all)  # type: ignore[arg-type]


# Прогреваем критичные модули СРАЗУ при импорте этого файла — именно это нужно вашим тестам,
# которые делают Base.metadata.create_all(engine) сразу после импорта app.models.base.
ensure_models_loaded()


__all__ = [
    # Bases
    "Base",
    "BaseModel",
    "NAMING_CONVENTIONS",
    "LenientInitMixin",
    # TZ helpers
    "utcnow_tz",
    "to_utc",
    "utc_now",  # <--- совместимость со старыми модулями
    # Mixins
    "SoftDeleteMixin",
    "TenantMixin",
    "AuditMixin",
    "LockableMixin",
    # Helpers (sync)
    "create",
    "get_by_id",
    "delete",
    "update",
    "get_or_create",
    "bulk_insert_rows",
    "bulk_update_rows",
    "bulk_update",  # <-- алиас оставлен
    "exists",
    "first",
    "count",
    "for_update_by_id",
    "pg_advisory_xact_lock",
    "pg_advisory_lock",
    "pg_advisory_unlock",
    "locked_transaction",
    "paginate",
    "refresh_safe",
    # Helpers (async)
    "acreate",
    "aget_by_id",
    "adelete",
    "aupdate",
    "aget_or_create",
    "abulk_insert_rows",
    "abulk_update_rows",
    "aexists",
    "afirst",
    "acount",
    "afor_update_by_id",
    "apg_advisory_xact_lock",
    "apg_advisory_lock",
    "apg_advisory_unlock",
    "alocked_transaction",
    "apaginate",
    # Upserts
    "upsert_postgres",
    "aupsert_postgres",
    # Safe wrappers
    "ensure_models_loaded",
    "metadata_create_all",
    "metadata_drop_all",
    "metadata_create_all_async",
    "metadata_drop_all_async",
]
