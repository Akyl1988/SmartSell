from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter
from sqlalchemy.engine.url import make_url
from sqlalchemy.exc import SQLAlchemyError

from app.core import config
from app.core.db import _get_async_engine  # type: ignore

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/_debug", tags=["debug"])


def _url_parts(url: str) -> dict[str, Any]:
    try:
        u = make_url(url)
        return {
            "driver": u.drivername or "",
            "user": u.username or "",
            "host": u.host or "",
            "port": u.port or "",
            "db": u.database or "",
        }
    except Exception:
        return {"driver": "", "user": "", "host": "", "port": "", "db": ""}


@router.get("/db")
async def debug_db() -> dict[str, Any]:
    settings = config.get_settings()
    url = settings.DATABASE_URL or ""
    parts = _url_parts(url)

    fp = config.db_connection_fingerprint(url, include_password=True)
    fp_no_pw = config.db_connection_fingerprint(url, include_password=False)
    source = settings.db_url_source() if hasattr(settings, "db_url_source") else "unknown"

    connectivity: dict[str, Any]
    try:
        eng = _get_async_engine()
        async with eng.connect() as conn:
            await conn.execute("SELECT 1")
        connectivity = {"status": "ok"}
    except SQLAlchemyError as e:  # pragma: no cover (needs live DB)
        connectivity = {
            "status": "failed",
            "error_class": e.__class__.__name__,
            "message": str(e).split("\n", 1)[0],
        }
    except Exception as e:  # pragma: no cover
        connectivity = {
            "status": "failed",
            "error_class": e.__class__.__name__,
            "message": str(e).split("\n", 1)[0],
        }

    return {
        "driver": parts["driver"],
        "user": parts["user"],
        "host": parts["host"],
        "port": parts["port"],
        "db": parts["db"],
        "source": source,
        "url_fp": fp,
        "url_no_pw_fp": fp_no_pw,
        "connectivity": connectivity,
    }
