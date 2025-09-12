# tests/conftest.py
import os
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.main import create_app

# База моделей проекта (у тебя папка app/models/*.py)
# В __init__.py должен экспортироваться Base, иначе импортируй Base из файла, где она объявлена.
from app.models import Base  # noqa: F401

# ✅ Для GitHub Actions лучше файловая SQLite, а не :memory:
SQLALCHEMY_DATABASE_URL = "sqlite:///./test.db"

# Для SQLite нужно отключить проверку потока
engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False})
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Если в проекте есть зависимость get_db — переопределим её в тестах.
def _override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture(scope="session", autouse=True)
def _create_test_db():
    # Создаем таблицы один раз на сессию тестов
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture(scope="session")
def app():
    application = create_app()
    application.state.TESTING = True

    # Переопределяем DI только если в проекте есть функция get_db
    try:
        from app.dependencies import get_db  # ← поправь путь, если у тебя иначе
        application.dependency_overrides[get_db] = _override_get_db
    except Exception:
        # Если зависимость не используется — просто продолжаем
        pass

    return application


@pytest.fixture()
def client(app):
    return TestClient(app)
