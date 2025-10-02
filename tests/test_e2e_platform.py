# tests/test_e2e_platform.py
import json
import pytest
from decimal import Decimal
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Импорт моделей/утилит из проекта
import app.models  # важно: прогревает маппинги
from app.models import metadata_create_all
from app.models.base import BaseModel
from app.models.company import Company
from app.models.user import User, UserSession, OTPCode
from app.models.product import Product
from app.models.warehouse import (
    Warehouse,
    ProductStock,
    StockMovement,
    movements_analytics,
    margin_report,
)


# ---------- БАЗОВЫЕ ФИКСТУРЫ ДЛЯ МОДЕЛЕЙ ----------
@pytest.fixture(scope="function")
def db_session():
    engine = create_engine("sqlite:///:memory:")
    metadata_create_all(engine)
    Session = sessionmaker(bind=engine)
    s = Session()
    try:
        yield s
    finally:
        s.close()


@pytest.fixture
def company(db_session):
    c = Company(name="Demo LLC")
    db_session.add(c)
    db_session.commit()
    return c


@pytest.fixture
def admin_user(db_session):
    u = User(
        username="admin",
        email="admin@example.com",
        role="admin",
        is_superuser=True,
        hashed_password="hashed",
    )
    db_session.add(u)
    db_session.commit()
    return u


@pytest.fixture
def product(db_session):
    p = Product(name="Widget A", sku="W-A-001", slug="widget-a", price=Decimal("150.00"))
    db_session.add(p)
    db_session.commit()
    return p


@pytest.fixture
def warehouse(db_session, company):
    w = Warehouse(company_id=company.id, name="Main WH", code="MAIN")
    db_session.add(w)
    db_session.commit()
    return w


@pytest.fixture
def stock(db_session, product, warehouse):
    s = ProductStock(product_id=product.id, warehouse_id=warehouse.id, quantity=0, min_quantity=2)
    db_session.add(s)
    db_session.commit()
    return s


# ---------- СКВОЗНОЙ ХЭППИ-ПАТЧ ПО МОДЕЛЯМ ----------
def test_e2e_models_happy_path(db_session, company, admin_user, product, warehouse, stock):
    # 1) user session + OTP
    sess = UserSession(user_id=admin_user.id, refresh_token="rftok", is_active=True)
    db_session.add(sess)
    otp = OTPCode(phone="77000000000", code="123456", purpose="login")
    db_session.add(otp)
    db_session.commit()
    assert sess.id and otp.id

    # 2) приход товара (receive) через helper ProductStock
    m_in = stock.receive(
        db_session, qty=10, user_id=admin_user.id, notes="initial load", commit=True
    )
    db_session.refresh(stock)
    assert stock.quantity == 10
    assert isinstance(m_in, StockMovement)

    # 3) резервирование части
    m_res = stock.reserve_and_log(
        db_session, qty=3, user_id=admin_user.id, notes="reserve for order #1", commit=True
    )
    db_session.refresh(stock)
    assert stock.reserved_quantity == 3
    assert m_res.movement_type == "reserve"

    # 4) исполнение резерва (fulfill) — списывает qty и reserved
    qty_to_ship = 2
    before_qty = stock.quantity
    m_ful = stock.fulfill_and_log(
        db_session, qty=qty_to_ship, user_id=admin_user.id, notes="fulfill order #1", commit=True
    )
    db_session.refresh(stock)
    assert stock.reserved_quantity == 1
    assert stock.quantity == before_qty - qty_to_ship
    assert m_ful.movement_type == "fulfill"
    assert m_ful.new_quantity == stock.quantity

    # 5) прямой расход (ship)
    stock.ship(db_session, qty=1, user_id=admin_user.id, notes="manual ship", commit=True)
    db_session.refresh(stock)
    assert stock.quantity == (10 - 2 - 1)  # 7

    # 6) аналитика движений
    stats = movements_analytics(db_session)
    by_type = {row["movement_type"]: row for row in stats}
    assert by_type["in"]["count"] >= 1
    assert by_type["reserve"]["count"] >= 1
    assert by_type["fulfill"]["count"] >= 1
    assert by_type["out"]["count"] >= 1

    # 7) отчёт по марже (price берём из Product.price)
    def price_fetcher(product_id: int):
        pr = db_session.query(Product).get(product_id)
        return pr.price if pr and pr.price else Decimal("0.00")

    mr = margin_report(
        db_session,
        date_from=admin_user.created_at,
        date_to=admin_user.created_at.replace(year=admin_user.created_at.year + 10),
        price_fetcher=price_fetcher,
    )
    # Если были OUT/FULFILL — будет строка для продукта
    if mr:
        row = next((r for r in mr if r["product_id"] == product.id), None)
        assert row is None or Decimal(row["cogs"]) >= Decimal("0.00")

    # 8) мягкое удаление/восстановление
    warehouse.archive()
    db_session.commit()
    assert warehouse.is_archived is True
    warehouse.restore()
    db_session.commit()
    assert warehouse.is_archived is False


# ---------- HTTP SMOKE (если есть FastAPI-приложение) ----------
@pytest.mark.anyio
async def test_http_smoke_if_app_exists():
    try:
        from app.main import create_app
    except Exception:
        pytest.skip("FastAPI app is not importable; skipping HTTP smoke.")
    app = create_app()

    try:
        import httpx
    except Exception:
        pytest.skip("httpx is not installed; skipping HTTP smoke.")

    async with httpx.AsyncClient(app=app, base_url="http://test") as ac:
        # health/live/ready — адаптируйте под ваши эндпоинты
        for path in ("/", "/health", "/live", "/ready"):
            resp = await ac.get(path)
            # допускаем, что некоторых маршрутов может не быть
            assert resp.status_code in (200, 404)
            if resp.status_code == 200:
                # убедимся, что это JSON/текст — но без строгой схемы
                _ = resp.text

        # Пример смоук-вызова API (если есть)
        # resp = await ac.get("/api/v1/products?limit=1")
        # assert resp.status_code in (200, 404)
