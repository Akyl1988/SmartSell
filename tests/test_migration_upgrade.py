from __future__ import annotations

import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

from app.core.config import settings


@pytest.mark.skipif(not settings.TEST_DATABASE_URL and not settings.DATABASE_URL, reason="Database URL not configured")
def test_alembic_upgrade_head_runs(tmp_path: Path):
    cfg = Config(str(Path("alembic.ini").resolve()))
    db_url = settings.TEST_DATABASE_URL or settings.DATABASE_URL
    if db_url:
        cfg.set_main_option("sqlalchemy.url", db_url)
    cfg.set_main_option("script_location", str(Path("migrations").resolve()))
    # Ensure env reads current working dir
    cwd = os.getcwd()
    try:
        os.chdir(Path(__file__).resolve().parent.parent)
        command.upgrade(cfg, "head")
    finally:
        os.chdir(cwd)
