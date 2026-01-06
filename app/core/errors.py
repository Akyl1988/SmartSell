from __future__ import annotations


def safe_error_message(exc: Exception, limit: int = 500) -> str:
    msg = str(exc) or exc.__class__.__name__
    return msg[:limit]
