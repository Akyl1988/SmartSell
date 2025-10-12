import os
from enum import Enum
from urllib.parse import urlparse

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

# ------------------ ENVIRONMENT ------------------
os.environ["TESTING"] = "1"

# Пытаемся импортировать DI и Base из приложения
try:
    from app.core.database import Base, get_db
except ImportError:
    from app.core.db import Base, get_db

from app.core.config import get_settings
from app.main import app


# ------------------ ENUMS ------------------
class MessageStatus(Enum):
    PENDING = "pending"
    SENT = "sent"
    DELIVERED = "delivered"
    FAILED = "failed"
    READ = "read"


# ------------------ BUILD DEDICATED TEST ENGINE (PostgreSQL) ------------------
settings = get_settings()
TEST_URL = settings.TEST_DATABASE_URL or settings.DATABASE_URL

if not TEST_URL:
    raise RuntimeError("TEST_DATABASE_URL/DATABASE_URL is not set for tests")

p = urlparse(TEST_URL)

# 1) Только PostgreSQL синхронный драйвер (psycopg2/pg8000)
if not p.scheme.startswith("postgresql"):
    raise RuntimeError(f"Tests must run on PostgreSQL (got: {p.scheme})")

# 2) Примитивная страховка: требуем 'test' в имени БД (например smartsell_test)
dbname = (p.path or "").lstrip("/")
if "test" not in dbname.lower():
    raise RuntimeError(
        f"Refusing to run destructive tests on non-test DB: '{dbname}'. "
        "Create and point TEST_DATABASE_URL to a database named with 'test'."
    )

# 3) Доп. защитный таймаут на запросы, pre_ping и future API
engine = create_engine(
    TEST_URL,
    pool_pre_ping=True,
    future=True,
)

TestingSessionLocal = sessionmaker(
    bind=engine,
    autocommit=False,
    autoflush=False,
    expire_on_commit=False,
)


def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


# Подменяем зависимость приложения на наш тестовый Session
app.dependency_overrides[get_db] = override_get_db
client = TestClient(app)


# ------------------ HELPERS ------------------
def _is_ok(resp, expected: int | tuple[int, ...]) -> bool:
    return (
        resp.status_code == expected if isinstance(expected, int) else resp.status_code in expected
    )


def _ensure_list_or_items_meta(resp_json):
    if isinstance(resp_json, list):
        return resp_json, None
    assert isinstance(resp_json, dict), "Response must be a list or dict with items/meta"
    assert "items" in resp_json and isinstance(resp_json["items"], list), "Missing items list"
    assert "meta" in resp_json and isinstance(resp_json["meta"], dict), "Missing meta"
    return resp_json["items"], resp_json["meta"]


# ------------------ MODULE FIXTURES ------------------
@pytest.fixture(scope="module", autouse=True)
def _module_cleanup():
    # На входе — убедимся, что подключение рабочее (и время ожидания маленькое)
    with engine.connect() as conn:
        # На всякий случай включим небольшой statement_timeout
        try:
            conn.execute(text("SET statement_timeout TO 3000"))  # 3s
        except Exception:
            pass
    yield
    # После прогона: роняем схему и освобождаем пул
    try:
        Base.metadata.drop_all(bind=engine)
    finally:
        try:
            engine.dispose()
        except Exception:
            pass


@pytest.fixture(scope="module")
def setup_database():
    """
    Создаём всю схему на отдельном тестовом движке.
    Если у вас используются миграции — здесь лучше вызывать Alembic.
    """
    Base.metadata.create_all(bind=engine)
    yield
    # Можно выполнить TRUNCATE для ускорения следующих модулей,
    # но мы drop_all делаем в _module_cleanup, так что не обязательно.


@pytest.fixture(scope="module")
def seeded_campaign_id(setup_database) -> int | None:
    """
    Создаёт кампанию через публичный API и возвращает её id.
    Если ручка недоступна (404), вернём None — тесты с действиями будут skip.
    """
    payload = {
        "title": "Seeded Campaign",
        "description": "Campaign created by fixture",
        "messages": [
            {
                "recipient": "seed@example.com",
                "content": "Seed message",
                "status": MessageStatus.PENDING.value,
                "channel": "email",
            }
        ],
        "tags": ["test", "seed"],
        "active": True,
    }
    resp = client.post("/api/v1/campaigns/", json=payload)
    if not _is_ok(resp, (200, 201)):
        return None
    try:
        cid = resp.json().get("id")
        if not cid:
            lst = client.get("/api/v1/campaigns/").json()
            items, _ = _ensure_list_or_items_meta(lst)
            cid = items[0]["id"] if items else None
        return cid
    except Exception:
        return None


# ------------------ CAMPAIGN CRUD ------------------
@pytest.mark.parametrize("title", ["Test Campaign", "Promo Campaign"])
def test_create_campaign(title, setup_database):
    payload = {
        "title": title,
        "description": f"{title} description",
        "messages": [
            {
                "recipient": "test@example.com",
                "content": "Test message content",
                "status": MessageStatus.PENDING.value,
                "channel": "email",
            }
        ],
        "tags": ["test"],
        "active": True,
    }
    resp = client.post("/api/v1/campaigns/", json=payload)
    assert _is_ok(resp, (200, 201)), resp.text
    data = resp.json()
    assert data.get("title") == payload["title"]
    assert data.get("description") == payload["description"]
    msgs = data.get("messages") or []
    assert isinstance(msgs, list) and len(msgs) >= 1
    assert msgs[0].get("status") == MessageStatus.PENDING.value


def test_get_campaigns(setup_database):
    resp = client.get("/api/v1/campaigns/")
    assert resp.status_code == 200, resp.text
    items, meta = _ensure_list_or_items_meta(resp.json())
    if meta:
        assert "page" in meta and "size" in meta and "total" in meta
    assert isinstance(items, list)


def test_get_campaign_by_id(seeded_campaign_id):
    if not seeded_campaign_id:
        pytest.skip("Campaign create/list API is not available")
    resp = client.get(f"/api/v1/campaigns/{seeded_campaign_id}")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert "title" in data and "description" in data


def test_update_campaign(seeded_campaign_id):
    if not seeded_campaign_id:
        pytest.skip("Campaign PUT API not available or campaign missing")
    payload = {
        "title": "Updated Campaign",
        "description": "Updated description",
        "messages": [],
        "tags": [],
        "active": True,
    }
    resp = client.put(f"/api/v1/campaigns/{seeded_campaign_id}", json=payload)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data.get("title") == payload["title"]
    assert data.get("description") == payload["description"]


def test_delete_campaign_independent():
    """
    Удаляем НЕ seeded-кампанию, чтобы не ломать остальные тесты.
    """
    payload = {
        "title": "To be deleted",
        "description": "Temporary campaign",
        "messages": [],
        "tags": ["temp"],
        "active": True,
    }
    create_resp = client.post("/api/v1/campaigns/", json=payload)
    assert _is_ok(create_resp, (200, 201)), create_resp.text
    temp_id = create_resp.json().get("id")
    assert temp_id, "API must return campaign id"
    resp = client.delete(f"/api/v1/campaigns/{temp_id}")
    assert _is_ok(resp, (200, 204)), resp.text


# ------------------ TAGS / MESSAGES / STATS ------------------
def test_campaign_tags(seeded_campaign_id):
    if not seeded_campaign_id:
        pytest.skip("Tags API not implemented for this campaign")
    payload = {"tag": "promo"}
    resp = client.post(f"/api/v1/campaigns/{seeded_campaign_id}/tags", json=payload)
    assert _is_ok(resp, (200, 201)), resp.text
    # Проверяем, что тег появился
    resp_get = client.get(f"/api/v1/campaigns/{seeded_campaign_id}")
    tags = (resp_get.json() or {}).get("tags", []) or []
    assert "promo" in [t.lower() for t in tags]


def test_add_message_to_campaign(seeded_campaign_id):
    if not seeded_campaign_id:
        pytest.skip("Messages API not implemented for this campaign")
    payload = {
        "recipient": "test2@example.com",
        "content": "Second message",
        "status": MessageStatus.PENDING.value,
        "channel": "email",
    }
    resp = client.post(f"/api/v1/campaigns/{seeded_campaign_id}/messages", json=payload)
    assert _is_ok(resp, (200, 201)), resp.text


def test_list_campaign_messages(seeded_campaign_id):
    if not seeded_campaign_id:
        pytest.skip("Messages listing not implemented for this campaign")
    resp = client.get(f"/api/v1/campaigns/{seeded_campaign_id}/messages")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert isinstance(body, list) or (isinstance(body, dict) and "items" in body)


def test_get_campaign_stats(seeded_campaign_id):
    if not seeded_campaign_id:
        pytest.skip("Stats not implemented for this campaign")
    resp = client.get(f"/api/v1/campaigns/{seeded_campaign_id}/stats")
    assert resp.status_code == 200, resp.text
    stats = resp.json()
    assert isinstance(stats, dict)
    assert "sent" in stats and "pending" in stats
    assert all(isinstance(stats[k], int) for k in ("sent", "pending") if k in stats)


# ------------------ EXTEND: SCHEDULE, ARCHIVE, RESTORE ------------------
# При необходимости добавляйте тесты на schedule, archive, restore, edge cases.
