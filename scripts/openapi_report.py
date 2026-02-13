from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

_HTTP_METHODS = {"get", "post", "put", "patch", "delete", "options", "head", "trace"}


def _load_openapi(path: Path) -> dict[str, Any]:
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise SystemExit(f"OpenAPI file not found: {path}") from exc

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Invalid JSON in OpenAPI file: {path}") from exc

    if not isinstance(data, dict):
        raise SystemExit(f"Invalid OpenAPI document (expected object): {path}")
    return data


def _iter_operations(spec: dict[str, Any]) -> list[tuple[str, str, dict[str, Any]]]:
    paths = spec.get("paths") or {}
    if not isinstance(paths, dict):
        return []

    out: list[tuple[str, str, dict[str, Any]]] = []
    for path, methods in paths.items():
        if not isinstance(methods, dict):
            continue
        for method, op in methods.items():
            if method.lower() not in _HTTP_METHODS:
                continue
            if not isinstance(op, dict):
                continue
            out.append((path, method.lower(), op))
    return out


def _format_security(op: dict[str, Any]) -> str:
    security = op.get("security")
    if not security:
        return "none"

    schemes: list[str] = []
    if isinstance(security, list):
        for item in security:
            if isinstance(item, dict):
                schemes.extend([str(key) for key in item.keys()])

    if not schemes:
        return "none"
    return ", ".join(sorted(set(schemes)))


def _has_http_bearer(op: dict[str, Any]) -> bool:
    security = op.get("security")
    if not isinstance(security, list):
        return False
    for item in security:
        if isinstance(item, dict) and "HTTPBearer" in item:
            return True
    return False


def build_report(spec: dict[str, Any]) -> str:
    ops = _iter_operations(spec)

    def _row(path: str, method: str, op: dict[str, Any]) -> str:
        summary = op.get("summary") or "-"
        operation_id = op.get("operationId") or "-"
        security = _format_security(op)
        return f"- `{method.upper()}` {path} - summary: {summary}; operationId: {operation_id}; security: {security}"

    admin_ops = [(p, m, o) for p, m, o in ops if p.startswith("/api/v1/admin/")]
    kaspi_ops = [(p, m, o) for p, m, o in ops if "kaspi" in (o.get("tags") or [])]
    auth_ops = [(p, m, o) for p, m, o in ops if _has_http_bearer(o)]

    admin_ops.sort(key=lambda item: (item[0], item[1]))
    kaspi_ops.sort(key=lambda item: (item[0], item[1]))
    auth_ops.sort(key=lambda item: (item[0], item[1]))

    lines: list[str] = ["# OpenAPI report", "", "## Admin endpoints"]
    if admin_ops:
        lines.extend([_row(p, m, o) for p, m, o in admin_ops])
    else:
        lines.append("- (none)")

    lines.extend(["", "## Kaspi endpoints"])
    if kaspi_ops:
        lines.extend([_row(p, m, o) for p, m, o in kaspi_ops])
    else:
        lines.append("- (none)")

    lines.extend(["", "## Auth"])
    if auth_ops:
        lines.extend([_row(p, m, o) for p, m, o in auth_ops])
    else:
        lines.append("- (none)")

    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate OpenAPI report.")
    parser.add_argument("path", nargs="?", default="./openapi.json")
    args = parser.parse_args()

    spec = _load_openapi(Path(args.path))
    report = build_report(spec)
    print(report, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
