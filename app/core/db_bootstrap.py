from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.logging import get_logger

log = get_logger(__name__)


def bootstrap_database(engine=None) -> None:
    """DEV-режим: создание схемы отключено, используйте Alembic."""
    raise RuntimeError("Database schema must be managed by Alembic, not create_all")


def ensure_seed_company(session: Session) -> int:
    """
    Создаёт базовую компанию и возвращает её id, если ни одной нет.
    Удобно для быстрого старта экспортов/демо.
    """
    from app.models.company import Company  # избегаем циклических импортов

    exists = session.execute(Company.__table__.select().limit(1)).first()
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
