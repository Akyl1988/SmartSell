from __future__ import annotations

import os
from typing import Any

from app.main_helpers import env_last_deploy_time, redact_dict


def build_info_payload(settings_obj: Any) -> dict[str, Any]:
    return {
        "version": settings_obj.VERSION,
        "git_sha": os.getenv("GIT_SHA", ""),
        "build_time": os.getenv("BUILD_TIME", ""),
        "build_number": os.getenv("BUILD_NUMBER", ""),
        "environment": settings_obj.ENVIRONMENT,
        "last_deploy_at": env_last_deploy_time(),
        "app_name": settings_obj.APP_NAME,
    }


def build_dbinfo_payload(settings_obj: Any) -> dict[str, Any]:
    safe_url = getattr(settings_obj, "DATABASE_URL_SAFE", None) or getattr(settings_obj, "DATABASE_URL", "")

    try:
        drv = (getattr(settings_obj, "sqlalchemy_urls", {}) or {}).get("driver") or "postgresql"
    except Exception:
        drv = "postgresql"

    return {
        "driver": drv,
        "url": safe_url,
        "status": "ok",
    }


def build_env_info_payload(settings_obj: Any) -> dict[str, Any]:
    env = redact_dict(dict(os.environ))
    safe_settings = redact_dict(
        {
            "APP_NAME": settings_obj.APP_NAME,
            "ENVIRONMENT": settings_obj.ENVIRONMENT,
            "VERSION": settings_obj.VERSION,
            "DEBUG": bool(settings_obj.DEBUG),
            "DATABASE_URL": getattr(settings_obj, "DATABASE_URL", None),
            "REDIS_URL": getattr(settings_obj, "REDIS_URL", None),
            "SMTP_HOST": getattr(settings_obj, "SMTP_HOST", None),
            "SMTP_PORT": getattr(settings_obj, "SMTP_PORT", None),
        }
    )
    return {"env": env, "settings": safe_settings}


def build_debug_headers_payload(request: Any) -> dict[str, Any]:
    headers = {k: v for k, v in request.headers.items()}
    return {"method": request.method, "url": str(request.url), "headers": headers}
