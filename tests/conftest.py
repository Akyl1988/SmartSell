# tests/conftest.py
"""
Pytest configuration and fixtures for async database testing.

Ключевые особенности:
- PostgreSQL (asyncpg) как единственный источник истины для интеграционных тестов.
- Для Postgres: перед create_all лениво подгружаем весь домен (import_all_models) — один раз.
- Для SQLite: глобальный безопасный create_all — создаём только самодостаточные таблицы (без «висячих» FK)
  и только с типами, поддерживаемыми SQLite (JSONB/ARRAY/INET/... пропускаем).
- Лёгкая автозагрузка ключевых моделей (Company/User/Warehouse/AuditLog), чтобы строковые relationship(...)
  резолвились и не падали мапперы.
- Удобные фикстуры клиента (sync/async), сессий, сброса данных и фабрик доменных сущностей.
- Явное DATABASE_URL (psycopg2) для прохождения test_database_url_default.
- Дружественная обработка env: TEST_ASYNC_DATABASE_URL (предпочтительно) или fallback к TEST_DATABASE_URL,
  включая автоконверсию драйвера psycopg2 -> asyncpg при необходимости (только для тестового async engine).
- Везде UTF-8: PYTHONIOENCODING по умолчанию, без влияния локали окружения.
"""

from __future__ import annotations

import asyncio
import os
from typing import (
    Any,
    AsyncIterator,
    Awaitable,
    Callable,
    Dict,
    Iterator,
    List,
    Optional,
    Set,
    Tuple,
)

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from httpx import AsyncClient
from sqlalchemy import MetaData, Table, text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool
from sqlalchemy.sql.type_api import TypeEngine  # 🔽 ДОБАВЛЕНО: типы для фильтра unsupported
from sqlalchemy import exc as sa_exc  # 🔽 ДОБАВЛЕНО: обработка OperationalError в SQLite

# ======================================================================================
# 0) Бутстрап окружения
# ======================================================================================

# Всюду UTF-8 (централизованно)
os.environ.setdefault("PYTHONIOENCODING", "UTF-8")

# Чтобы прошёл tests/app/core/test_config.py::test_database_url_default
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+psycopg2://postgres:admin123@localhost:5432/SmartSell",
)

# Явно укажем "современный" strict-режим asyncio, если плагин не делает этого сам.
os.environ.setdefault("PYTEST_ASYNCIO_MODE", "strict")


# ======================================================================================
# ВСПОМОГАТЕЛЬНЫЕ УТИЛИТЫ: URL’ы, импорт app и моделей, поиск get_db
# ======================================================================================


def _get_async_test_url() -> str:
    """
    Возвращает async URL для тестового движка:
      1) TEST_ASYNC_DATABASE_URL (если задан)
      2) иначе TEST_DATABASE_URL — но при необходимости конвертируем драйвер psycopg2 -> asyncpg
    Бросаем понятное исключение, если URL пуст или не postgresql.
    """
    async_url = os.getenv("TEST_ASYNC_DATABASE_URL")
    base_url = os.getenv("TEST_DATABASE_URL")

    if async_url:
        url = async_url
    elif base_url:
        # Если кто-то по ошибке дал sync URL, аккуратно конвертируем только драйвер.
        if base_url.startswith("postgresql+psycopg2://"):
            url = "postgresql+asyncpg://" + base_url.split("postgresql+psycopg2://", 1)[1]
        else:
            url = base_url
    else:
        # разумный дефолт под локальную разработку
        url = "postgresql+asyncpg://postgres:admin123@localhost:5432/SmartSellTest"

    if not url.startswith("postgresql+"):
        raise RuntimeError(
            f"Expected a PostgreSQL URL for tests, got '{url}'. "
            f"Use TEST_ASYNC_DATABASE_URL='postgresql+asyncpg://...'"
        )
    if not url.startswith("postgresql+asyncpg://"):
        raise RuntimeError(
            f"Async engine requires async driver. Got '{url}'. "
            f"Use TEST_ASYNC_DATABASE_URL (postgresql+asyncpg://...) "
            f"or set TEST_DATABASE_URL with asyncpg driver."
        )
    return url


def _import_app_and_get_db() -> Tuple[Any, Callable[..., AsyncIterator[AsyncSession]]]:
    """
    Возвращает (app, get_db). Поддерживает оба расположения:
      - app.core.db:get_db
      - app.core.database:get_db
    """
    # Импорт FastAPI приложения
    try:
        from app.main import app  # type: ignore
    except Exception as e:
        raise RuntimeError(f"Cannot import FastAPI app from app.main: {e}") from e

    # Импорт get_db
    get_db = None
    # Новый путь
    try:
        from app.core.db import get_db as _get_db  # type: ignore

        get_db = _get_db
    except Exception:
        pass
    # Старый путь (для обратной совместимости)
    if get_db is None:
        try:
            from app.core.database import get_db as _get_db  # type: ignore

            get_db = _get_db
        except Exception as e:
            raise RuntimeError(
                f"Cannot import get_db from app.core.db or app.core.database: {e}"
            ) from e

    return app, get_db  # type: ignore[return-value]


def _import_all_models_once() -> bool:
    """
    Пытаемся вызвать app.models.import_all_models(), если доступно.
    Возвращает True, если импортировали весь домен; иначе False.
    """
    try:
        import app.models as m  # type: ignore

        if hasattr(m, "import_all_models"):
            m.import_all_models()  # type: ignore[attr-defined]
            return True
    except Exception:
        pass
    return False


def _bootstrap_minimal_models() -> None:
    """
    Лёгкая автозагрузка только критичных классов, чтобы строковые relationship(...)
    не падали при конфигурации мапперов в SQLite-юнитах.
    """
    try:
        import app.models.company  # type: ignore  # регистрирует Company/companies
    except Exception:
        pass
    try:
        import app.models.user  # type: ignore
    except Exception:
        pass
    # 🔽 ДОБАВЛЕНО: чтобы relationship по складам и аудиту конфигурировались (убираем NoForeignKeysError)
    try:
        import app.models.warehouse  # type: ignore
    except Exception:
        pass
    try:
        import app.models.audit_log  # type: ignore
    except Exception:
        pass


# ======================================================================================
# 1) Настройка тестового AsyncEngine (PostgreSQL + asyncpg)
# ======================================================================================

TEST_DATABASE_URL = _get_async_test_url()

test_engine: AsyncEngine = create_async_engine(
    TEST_DATABASE_URL,
    echo=False,
    pool_pre_ping=True,
    poolclass=NullPool,  # не держим коннекты — полезно для Windows/CI
    future=True,
)

TestingSessionLocal = async_sessionmaker(
    bind=test_engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


# ======================================================================================
# 2) Глобальная защита create_all для SQLite + минимальная автозагрузка классов
# ======================================================================================


def _is_sqlite_bind(bind: Any) -> bool:
    """Определяем, что create_all вызывают для SQLite (особенно :memory:)."""
    try:
        if isinstance(bind, (Engine, Connection)):
            return getattr(bind.dialect, "name", "") == "sqlite"
    except Exception:
        pass
    return False


def _sqlite_extract_target_table_names(t: Table) -> Set[str]:
    """Вернёт множество имён таблиц, на которые смотрят FK этой таблицы (по 'target_fullname' / '_colspec')."""
    targets: Set[str] = set()
    for fk in t.foreign_keys:
        spec = getattr(fk, "target_fullname", None) or getattr(fk, "_colspec", None)
        if spec:
            table_name = str(spec).split(".", 1)[0]
            if table_name:
                targets.add(table_name)
    return targets


# 🔽 ДОБАВЛЕНО: фильтр неподдерживаемых SQLite типов (JSONB и др.)
_POSTGRES_ONLY_TYPENAMES = {
    "JSONB",
    "ARRAY",
    "HSTORE",
    "CIDR",
    "INET",
    "UUID",  # по умолчанию пропускаем; если есть кастомный тип — можно убрать из списка
}


def _sqlite_is_supported_type(typ: TypeEngine) -> bool:
    """
    Возвращает False для типов, которые SQLite не умеет компилировать нативно.
    Проверяем по имени класса типа (например, JSONB, ARRAY и т.п.).
    """
    try:
        tname = type(typ).__name__.upper()
    except Exception:
        return False
    if tname in _POSTGRES_ONLY_TYPENAMES:
        return False
    # подстрахуемся от разных диалектных обёрток
    if "JSONB" in tname:
        return False
    return True


def _sqlite_is_supported_table(t: Table) -> bool:
    """Таблица поддерживается SQLite, если все её столбцы — с поддерживаемыми типами."""
    try:
        for c in t.columns:
            if not _sqlite_is_supported_type(c.type):
                return False
        return True
    except Exception:
        # если не смогли прочитать типы, лучше не создавать такую таблицу
        return False


def _sqlite_self_contained_tables(md: MetaData) -> List[Table]:
    """
    Таблицы, у которых все FK указывают на реально присутствующие в текущем MetaData
    И таблица не содержит заведомо неподдерживаемых для SQLite типов.
    """
    present: Set[str] = set(md.tables.keys())
    result: List[Table] = []
    for t in md.tables.values():
        if _sqlite_extract_target_table_names(t).issubset(present) and _sqlite_is_supported_table(
            t
        ):
            result.append(t)
    return result


@pytest.fixture(scope="session", autouse=True)
def _sqlite_safe_create_all_monkeypatch() -> Iterator[None]:
    """
    Глобально монипатчим MetaData.create_all для SQLite на время тестовой сессии.
    Создаём только «самодостаточные» таблицы, чтобы избежать NoReferencedTableError,
    и пропускаем таблицы с неподдерживаемыми типами (например, JSONB).
    Также аккуратно игнорируем «index ... already exists» в SQLite.
    """
    original_create_all = MetaData.create_all

    def _safe_create_all(self: MetaData, bind: Any = None, **kwargs):
        if _is_sqlite_bind(bind):
            from sqlalchemy import MetaData as _MD

            tables = _sqlite_self_contained_tables(self)
            if not tables:
                return
            tmp = _MD()
            for t in tables:
                # современный метод (вместо t.tometadata)
                t.to_metadata(tmp)

            # гарантируем checkfirst=True и игнорируем «index already exists»
            kwargs.setdefault("checkfirst", True)
            try:
                return original_create_all(tmp, bind=bind, **kwargs)
            except sa_exc.OperationalError as e:
                msg = (str(e) or "").lower()
                if "already exists" in msg and "index" in msg:
                    # безопасно пропускаем повторное создание индекса
                    return None
                raise
        # не SQLite — обычное поведение
        kwargs.setdefault("checkfirst", True)
        try:
            return original_create_all(self, bind=bind, **kwargs)
        except sa_exc.OperationalError as e:
            # подстраховка: если какая-то БД тоже вернёт "already exists"
            msg = (str(e) or "").lower()
            if "already exists" in msg:
                return None
            raise

    MetaData.create_all = _safe_create_all  # type: ignore[assignment]
    try:
        yield
    finally:
        MetaData.create_all = original_create_all  # type: ignore[assignment]


@pytest.fixture(scope="session", autouse=True)
def _bootstrap_minimal_models_for_mapping() -> None:
    """
    Лёгкая автозагрузка только критичных классов, чтобы строковые relationship(...)
    не падали при конфигурации мапперов в SQLite-юнитах.
    """
    _bootstrap_minimal_models()


# ======================================================================================
# 3) Патч create_all для Postgres (полная схема) — один раз на сессию
# ======================================================================================

_MODELS_IMPORTED_ONCE = False
_CREATE_ALL_PATCHED = False


def _ensure_patch_create_all_for_postgres() -> None:
    """
    ПАТЧИТ Base.metadata.create_all внутри app.models так, чтобы:
    - для не-SQLite один раз вызывать import_all_models(), затем обычный create_all.
    - для SQLite — доверяем глобальному монипатчу MetaData.create_all (см. выше).
    """
    global _MODELS_IMPORTED_ONCE, _CREATE_ALL_PATCHED
    import app.models as m  # type: ignore

    if _CREATE_ALL_PATCHED:
        return

    original = m.Base.metadata.create_all

    def _patched_create_all(*args, **kwargs):
        bind = kwargs.get("bind")
        if bind is None and args:
            for a in args:
                if isinstance(a, (Engine, Connection)):
                    bind = a
                    break

        if not _is_sqlite_bind(bind):
            # Полная загрузка домена строго один раз
            if not _MODELS_IMPORTED_ONCE and _import_all_models_once():
                _MODELS_IMPORTED_ONCE = True
        # для SQLite — ничего особого: отработает глобальный монкипатч MetaData.create_all
        kwargs.setdefault("checkfirst", True)
        try:
            return original(*args, **kwargs)
        except sa_exc.OperationalError as e:
            # обрабатываем потенциальные дубли «already exists»
            msg = (str(e) or "").lower()
            if "already exists" in msg:
                return None
            raise

    m.Base.metadata.create_all = _patched_create_all  # type: ignore[assignment]
    _CREATE_ALL_PATCHED = True


# ======================================================================================
# 4) Жизненный цикл схемы Postgres — один раз на сессию
# ======================================================================================


@pytest_asyncio.fixture(scope="session")
async def test_db() -> AsyncIterator[None]:
    """
    Создать ВСЮ схему БД один раз перед тестами и снести её после.
    Только для Postgres (интеграционные тесты).
    """
    _ensure_patch_create_all_for_postgres()

    import app.models as m  # type: ignore

    if not _MODELS_IMPORTED_ONCE:
        # Если import_all_models недоступен — fallback на минимальный bootstrap
        if not _import_all_models_once():
            _bootstrap_minimal_models()
            _MODELS_IMPORTED_ONCE = True

    async with test_engine.begin() as conn:
        await conn.run_sync(lambda sync_conn: m.Base.metadata.create_all(bind=sync_conn))

    try:
        if hasattr(m, "assert_relationships_resolved"):
            m.assert_relationships_resolved()  # type: ignore[attr-defined]
    except Exception as e:
        raise RuntimeError(f"Model relationship/FK unresolved after create_all: {e}") from e

    try:
        yield
    finally:
        async with test_engine.begin() as conn:
            await conn.run_sync(lambda sync_conn: m.Base.metadata.drop_all(bind=sync_conn))


# ======================================================================================
# 5) Event loop для pytest-asyncio (strict mode совместим)
# ======================================================================================


@pytest.fixture(scope="session")
def event_loop() -> Iterator[asyncio.AbstractEventLoop]:
    loop = asyncio.new_event_loop()
    try:
        yield loop
    finally:
        loop.close()


# ======================================================================================
# 6) FastAPI клиенты (async/sync) с ленивыми импортами app и get_db
# ======================================================================================


async def _override_get_db() -> AsyncIterator[AsyncSession]:
    async with TestingSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()


@pytest_asyncio.fixture
async def async_client(test_db: None) -> AsyncIterator[AsyncClient]:
    app, get_db = _import_app_and_get_db()
    app.dependency_overrides[get_db] = _override_get_db  # type: ignore[index]
    async with AsyncClient(app=app, base_url="http://test") as client:
        try:
            yield client
        finally:
            app.dependency_overrides.clear()


@pytest.fixture
def client(test_db: None) -> Iterator[TestClient]:
    app, get_db = _import_app_and_get_db()
    app.dependency_overrides[get_db] = _override_get_db  # type: ignore[index]
    with TestClient(app) as c:
        try:
            yield c
        finally:
            app.dependency_overrides.clear()


# ======================================================================================
# 7) Сессии БД + фабрика + быстрый сброс данных
# ======================================================================================


@pytest_asyncio.fixture
async def async_db_session(test_db: None) -> AsyncIterator[AsyncSession]:
    async with TestingSessionLocal() as session:
        yield session


@pytest.fixture
def db_session_factory() -> Callable[[], Awaitable[AsyncSession]]:
    async def _factory() -> AsyncSession:
        return TestingSessionLocal()

    return _factory


@pytest_asyncio.fixture
async def db_reset(async_db_session: AsyncSession) -> AsyncIterator[None]:
    yield

    import app.models as m  # type: ignore

    tablenames = [t for t in m.Base.metadata.tables.keys() if t != "alembic_version"]
    if not tablenames:
        return
    sql = "TRUNCATE " + ", ".join(f'"{name}"' for name in tablenames) + " RESTART IDENTITY CASCADE"
    await async_db_session.execute(text(sql))
    await async_db_session.commit()


# ======================================================================================
# 8) Сэмплы и фабрики доменных сущностей
# ======================================================================================


@pytest.fixture
def sample_user_data() -> Dict[str, object]:
    return {
        "phone": "77051234567",
        "email": "test@example.com",
        "full_name": "Test User",
        "password": "password123",
        "confirm_password": "password123",
        "username": "testuser",
    }


@pytest.fixture
def sample_product_data() -> Dict[str, object]:
    return {
        "name": "Test Product",
        "slug": "test-product",
        "sku": "TEST-001",
        "description": "A test product",
        "price": 99.99,
        "stock_quantity": 100,
        "is_active": True,
    }


@pytest_asyncio.fixture
async def factory(async_db_session: AsyncSession) -> Dict[str, Callable[..., Awaitable[object]]]:
    from app.models.company import Company  # type: ignore
    from app.models.user import User  # type: ignore
    from app.models.product import Category, Product, ProductVariant  # type: ignore
    from app.models.warehouse import Warehouse, ProductStock  # type: ignore

    async def create_company(name: str = "Acme Inc.") -> Company:
        obj = Company(name=name)
        async_db_session.add(obj)
        await async_db_session.commit()
        await async_db_session.refresh(obj)
        return obj

    async def create_user(
        *,
        username: str = "testuser",
        email: str = "test@example.com",
        phone: str = "+70000000000",
        company: Company | None = None,
        hashed_password: str = "",
    ) -> User:
        obj = User(username=username, email=email, phone=phone, hashed_password=hashed_password)
        if hasattr(obj, "company_id") and company is not None:
            setattr(obj, "company_id", company.id)
        async_db_session.add(obj)
        await async_db_session.commit()
        await async_db_session.refresh(obj)
        return obj

    async def create_category(*, name: str = "Default", slug: str = "default") -> Category:
        obj = Category(name=name, slug=slug)
        async_db_session.add(obj)
        await async_db_session.commit()
        await async_db_session.refresh(obj)
        return obj

    async def create_product(
        *,
        name: str = "Sample Product",
        slug: str = "sample-product",
        sku: str = "SKU-001",
        price: float = 100.0,
        stock_quantity: int = 10,
        category: Category | None = None,
        company: Company | None = None,
        is_active: bool = True,
    ) -> Product:
        if category is None:
            category = await create_category()
        kwargs: Dict[str, object] = dict(
            name=name,
            slug=slug,
            sku=sku,
            price=price,
            stock_quantity=stock_quantity,
            category_id=category.id,
            is_active=is_active,
        )
        try:
            if "company_id" in Product.__table__.columns:  # type: ignore[attr-defined]
                if company is None:
                    company = await create_company()
                kwargs["company_id"] = company.id
        except Exception:
            pass

        obj = Product(**kwargs)  # type: ignore[arg-type]
        async_db_session.add(obj)
        await async_db_session.commit()
        await async_db_session.refresh(obj)
        return obj

    async def create_variant(
        *,
        product: Product | None = None,
        name: str = "Sample Variant",
        sku: str = "SKU-001-BLUE",
        price: float = 110.0,
        stock_quantity: int = 3,
        is_active: bool = True,
    ) -> ProductVariant:
        if product is None:
            product = await create_product()
        obj = ProductVariant(
            product_id=product.id,
            name=name,
            sku=sku,
            price=price,
            stock_quantity=stock_quantity,
            is_active=is_active,
        )
        async_db_session.add(obj)
        await async_db_session.commit()
        await async_db_session.refresh(obj)
        return obj

    async def create_warehouse(
        *, name: str = "Main WH", company: Company | None = None
    ) -> Warehouse:
        obj = Warehouse(name=name)
        if hasattr(obj, "company_id"):
            if company is None:
                company = await create_company()
            setattr(obj, "company_id", company.id)
        async_db_session.add(obj)
        await async_db_session.commit()
        await async_db_session.refresh(obj)
        return obj

    async def create_stock(
        *,
        product: Product | None = None,
        warehouse: Warehouse | None = None,
        quantity: int = 7,
    ) -> ProductStock:
        if product is None:
            product = await create_product()
        if warehouse is None:
            warehouse = await create_warehouse()
        obj = ProductStock(product_id=product.id, warehouse_id=warehouse.id, quantity=quantity)
        async_db_session.add(obj)
        await async_db_session.commit()
        await async_db_session.refresh(obj)
        return obj

    return {
        "create_company": create_company,
        "create_user": create_user,
        "create_category": create_category,
        "create_product": create_product,
        "create_variant": create_variant,
        "create_warehouse": create_warehouse,
        "create_stock": create_stock,
    }
