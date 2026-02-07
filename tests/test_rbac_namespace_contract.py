from __future__ import annotations

from collections.abc import Callable, Iterable

from fastapi.routing import APIRoute

from app.core.dependencies import require_platform_admin
from app.main import app


def _iter_calls(dep) -> Iterable[Callable]:
    if dep is None:
        return []
    call = getattr(dep, "call", None)
    if call is None:
        return []
    stack = [call]
    seen: set[int] = set()
    out: list[Callable] = []
    while stack:
        fn = stack.pop()
        if id(fn) in seen:
            continue
        seen.add(id(fn))
        out.append(fn)
        wrapped = getattr(fn, "__wrapped__", None)
        if wrapped is not None:
            stack.append(wrapped)
    return out


def _call_id(fn: Callable) -> str:
    mod = getattr(fn, "__module__", "") or ""
    qual = getattr(fn, "__qualname__", "") or getattr(fn, "__name__", "") or "<callable>"
    return f"{mod}.{qual}".strip(".")


def _collect_dependency_calls(route: APIRoute) -> set[str]:
    calls: set[str] = set()

    def walk(dep) -> None:
        for fn in _iter_calls(dep):
            calls.add(_call_id(fn))
        for child in getattr(dep, "dependencies", []) or []:
            walk(child)

    walk(route.dependant)
    return calls


def test_rbac_namespace_contract():
    admin_prefixes = ("/api/v1/admin", "/api/admin")
    platform_guard_id = _call_id(require_platform_admin)

    missing_admin_guard: list[str] = []
    store_routes_with_admin_guard: list[str] = []

    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        path = route.path or ""
        if not path.startswith("/api/"):
            continue

        calls = _collect_dependency_calls(route)
        has_platform_guard = platform_guard_id in calls

        if path.startswith(admin_prefixes):
            if not has_platform_guard:
                missing_admin_guard.append(path)
        elif path.startswith("/api/v1/"):
            if has_platform_guard:
                store_routes_with_admin_guard.append(path)

    assert not missing_admin_guard, f"Admin routes missing require_platform_admin: {sorted(set(missing_admin_guard))}"
    assert not store_routes_with_admin_guard, (
        "Non-admin /api/v1 routes must not include require_platform_admin: "
        f"{sorted(set(store_routes_with_admin_guard))}"
    )
