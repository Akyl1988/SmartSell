from __future__ import annotations

import argparse
import os
import sys
from typing import Any

from sqlalchemy import create_engine, text


def _to_sync_url(url: str) -> str:
    if url.startswith("postgresql+asyncpg://"):
        return "postgresql+psycopg2://" + url.split("postgresql+asyncpg://", 1)[1]
    if url.startswith("postgresql+psycopg://"):
        return "postgresql+psycopg2://" + url.split("postgresql+psycopg://", 1)[1]
    return url


from app.core.config import get_settings
from app.core.security import get_password_hash


def _is_dev_or_test(settings: Any) -> bool:
    try:
        if getattr(settings, "is_testing", False):
            return True
        if getattr(settings, "is_development", False):
            return True
    except Exception:
        pass
    env = (os.getenv("ENVIRONMENT") or "").strip().lower()
    return env in {"development", "dev", "local", "testing", "test"} or bool(os.getenv("TESTING"))


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Reset user password (dev-only CLI).")
    parser.add_argument("--identifier", required=True, help="User phone or email")
    parser.add_argument("--password", required=True, help="New password")
    parser.add_argument("--unlock", action="store_true", help="Reset lock counters and unlock user")
    return parser.parse_args(argv)


def _reset_password(identifier: str, password: str, unlock: bool) -> int:
    settings = get_settings()
    if not _is_dev_or_test(settings):
        sys.stderr.write("ERR: reset-password CLI is dev/test only\n")
        return 1

    db_url = getattr(settings, "DATABASE_URL", None) or os.getenv("DATABASE_URL")
    if not db_url:
        sys.stderr.write("ERR: DATABASE_URL is required\n")
        return 1

    engine = create_engine(_to_sync_url(db_url))
    hashed = get_password_hash(password)
    with engine.begin() as conn:
        row = (
            conn.execute(
                text(
                    "SELECT id, phone, email FROM public.users "
                    "WHERE phone = :identifier OR email = :identifier "
                    "LIMIT 1"
                ),
                {"identifier": identifier},
            )
            .mappings()
            .first()
        )

        if not row:
            return 2

        params = {
            "uid": int(row["id"]),
            "hashed_password": hashed,
        }
        if unlock:
            conn.execute(
                text(
                    "UPDATE public.users "
                    "SET hashed_password = :hashed_password, "
                    "failed_login_attempts = 0, "
                    "locked_until = NULL, "
                    "locked_at = NULL "
                    "WHERE id = :uid"
                ),
                params,
            )
        else:
            conn.execute(
                text("UPDATE public.users SET hashed_password = :hashed_password WHERE id = :uid"),
                params,
            )

    sys.stdout.write(f"user_id={row['id']} identifier={identifier} updated unlock={bool(unlock)}\n")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv or sys.argv[1:])
    return _reset_password(args.identifier, args.password, bool(args.unlock))


if __name__ == "__main__":
    raise SystemExit(main())
