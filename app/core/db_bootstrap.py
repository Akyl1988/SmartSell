from __future__ import annotations
import os
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from app.core.db import Base, engine as default_engine  # не трогаем существующую инфраструктуру
from app.core.logging import get_logger

log = get_logger(__name__)

def bootstrap_database(engine=None) -> None:
    """DEV-режим: создать таблицы, если их нет. Не трогает прод."""
    if os.getenv("SMARTSELL_SKIP_ALEMBIC", "0") != "1":
        log.info("DB bootstrap skipped (SMARTSELL_SKIP_ALEMBIC!=1)")
        return
    engine = engine or default_engine
    log.info("Running metadata.create_all() (DEV only)")
    Base.metadata.create_all(bind=engine)

def ensure_seed_company(session: Session) -> int:
    """
    Создаёт базовую компанию и возвращает её id, если ни одной нет.
    Удобно для быстрого старта экспортов/демо.
    """
    from app.models.company import Company  # избегаем циклических импортов
    exists = session.execute(
        Company.__table__.select().limit(1)
    ).first()
    if exists:
        # вернуть любую существующую
        row = session.execute(Company.__table__.select().limit(1)).first()
        return row.id if row else 1
    # создаём демо-компанию
    demo = Company(
        name="Demo LLC",
        is_active=True,
        subscription_plan="start",
        settings='{"kaspi":{"exclusions":{"friend_store_ids":[]}}}',
    )
    session.add(demo)
    session.commit()
    return demo.id
