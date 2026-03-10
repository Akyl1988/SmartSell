from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession


class KaspiProbeOut(BaseModel):
    ok: bool
    status_code: int | None = None
    error_class: str | None = None
    message: str | None = None
    elapsed_ms: int


class KaspiSchemaProbeItem(BaseModel):
    path: str
    url: str
    ok: bool
    status_code: int | None = None
    error_class: str | None = None
    message: str | None = None
    response_snippet: str | None = None
    elapsed_ms: int


class KaspiSchemaProbeOut(BaseModel):
    ok: bool
    items: list[KaspiSchemaProbeItem]


class KaspiFeedUploadProbeItem(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    path: str
    url: str
    ok: bool
    status_code: int | None = None
    error_class: str | None = None
    message: str | None = None
    response_snippet: str | None = Field(None, alias="snippet")
    location: str | None = None
    import_code: str | None = None
    elapsed_ms: int


class KaspiFeedUploadProbeOut(BaseModel):
    ok: bool
    items: list[KaspiFeedUploadProbeItem]


def register_kaspi_debug_routes(
    router: APIRouter,
    *,
    auth_dependency: Any,
    get_async_db_dependency: Any,
    require_platform_admin_fn: Callable[[Any], None],
    require_store_admin_company_scoped_fn: Callable[[Any], Awaitable[Any]],
    kaspi_store_token_model: Any,
    build_kaspi_httpx_client_fn: Callable[[], Any],
    build_kaspi_orders_params_fn: Callable[..., dict[str, Any]],
    is_dev_environment_fn: Callable[[], bool],
    kaspi_user_agent_fn: Callable[[], str],
    probe_response_snippet_fn: Callable[..., str],
    log_kaspi_probe_error_fn: Callable[..., None],
    log_kaspi_probe_response_fn: Callable[..., None],
    safe_log_info_fn: Callable[..., None],
    kaspi_ns: str,
    fast_probe_timeout: float,
) -> None:
    @router.get("/_debug/ping", summary="Kaspi debug ping")
    def kaspi_debug_ping():
        return {"ok": True, "module": "kaspi", "prefix": router.prefix}

    @router.get("/_debug/probe", summary="Kaspi debug probe", response_model=KaspiProbeOut)
    async def kaspi_debug_probe(
        request: Request,
        store_name: str = Query(..., min_length=1, alias="store_name"),
        state: str | None = Query(None),
        current_user: Any = Depends(auth_dependency),
        session: AsyncSession = Depends(get_async_db_dependency),
    ):
        require_platform_admin_fn(current_user)
        token = await kaspi_store_token_model.get_token(session, store_name)
        if not token:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="kaspi_token_not_found")

        now = datetime.utcnow()
        ge_ms = int((now - timedelta(days=2)).timestamp() * 1000)
        le_ms = int(now.timestamp() * 1000)

        url = "https://kaspi.kz/shop/api/v2/orders"
        params = build_kaspi_orders_params_fn(
            ge_ms=ge_ms,
            le_ms=le_ms,
            state=state or "NEW",
            page_number=0,
            page_size=1,
        )
        headers = {
            "X-Auth-Token": token,
            "Accept": "application/vnd.api+json",
        }

        request_id = getattr(getattr(request, "state", None), "request_id", None)
        started = time.perf_counter()
        try:
            async with build_kaspi_httpx_client_fn() as client:
                resp = await client.get(url, headers=headers, params=params)
        except httpx.TimeoutException as exc:
            log_kaspi_probe_error_fn(
                request_id=request_id,
                company_id=None,
                store_name=store_name,
                method="GET",
                url=url,
                exc=exc,
            )
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            return KaspiProbeOut(
                ok=False,
                status_code=None,
                error_class=type(exc).__name__,
                message=str(exc),
                elapsed_ms=elapsed_ms,
            )
        except httpx.RequestError as exc:
            log_kaspi_probe_error_fn(
                request_id=request_id,
                company_id=None,
                store_name=store_name,
                method="GET",
                url=url,
                exc=exc,
            )
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            return KaspiProbeOut(
                ok=False,
                status_code=None,
                error_class=type(exc).__name__,
                message=str(exc),
                elapsed_ms=elapsed_ms,
            )

        elapsed_ms = int((time.perf_counter() - started) * 1000)
        log_kaspi_probe_response_fn(
            request_id=request_id,
            company_id=None,
            store_name=store_name,
            method="GET",
            url=url,
            status_code=resp.status_code,
            response_text=resp.text,
            elapsed_ms=elapsed_ms,
        )
        ok = 200 <= resp.status_code < 300
        return KaspiProbeOut(
            ok=ok,
            status_code=resp.status_code,
            error_class=None if ok else "HTTPStatusError",
            message=None if ok else "upstream_response",
            elapsed_ms=elapsed_ms,
        )

    @router.get("/_debug/schema-probe", summary="Kaspi schema probe", response_model=KaspiSchemaProbeOut)
    async def kaspi_schema_probe(
        request: Request,
        store_name: str = Query(..., min_length=1, alias="store_name"),
        paths: list[str] | None = Query(None),
        current_user: Any = Depends(auth_dependency),
        session: AsyncSession = Depends(get_async_db_dependency),
    ):
        if not is_dev_environment_fn():
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not_found")

        require_platform_admin_fn(current_user)
        token = await kaspi_store_token_model.get_token(session, store_name)
        if not token:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="kaspi_token_not_found")

        default_paths = [
            "/shop/api/products/import/schema",
            "/shop/api/products/prices/import/schema",
            "/shop/api/products/price/import/schema",
            "/shop/api/products/import/prices/schema",
            "/shop/api/prices/import/schema",
            "/shop/api/products/availability/import/schema",
            "/shop/api/products/stocks/import/schema",
            "/shop/api/products/stock/import/schema",
            "/shop/api/products/import/stocks/schema",
            "/shop/api/stocks/import/schema",
        ]
        candidate_paths = paths or default_paths
        headers = {
            "X-Auth-Token": token,
            "Accept": "application/json",
            "User-Agent": kaspi_user_agent_fn(),
        }

        items: list[KaspiSchemaProbeItem] = []
        async with httpx.AsyncClient(timeout=fast_probe_timeout, follow_redirects=True) as client:
            for path in candidate_paths:
                normalized = path if path.startswith("/") else f"/{path}"
                url = f"https://kaspi.kz{normalized}"
                started = time.perf_counter()
                try:
                    resp = await client.get(url, headers=headers)
                    elapsed_ms = int((time.perf_counter() - started) * 1000)
                    items.append(
                        KaspiSchemaProbeItem(
                            path=normalized,
                            url=url,
                            ok=200 <= resp.status_code < 300,
                            status_code=resp.status_code,
                            response_snippet=probe_response_snippet_fn(resp.text, 200),
                            elapsed_ms=elapsed_ms,
                        )
                    )
                except (httpx.TimeoutException, httpx.RequestError) as exc:
                    elapsed_ms = int((time.perf_counter() - started) * 1000)
                    items.append(
                        KaspiSchemaProbeItem(
                            path=normalized,
                            url=url,
                            ok=False,
                            status_code=None,
                            error_class=type(exc).__name__,
                            message=str(exc),
                            response_snippet=None,
                            elapsed_ms=elapsed_ms,
                        )
                    )

        ok = bool(items) and all(item.ok for item in items)
        request_id = getattr(getattr(request, "state", None), "request_id", None)
        safe_log_info_fn(
            "kaspi_schema_probe",
            request_id=request_id,
            store_name=store_name,
            ok=ok,
            total=len(items),
        )
        return KaspiSchemaProbeOut(ok=ok, items=items)

    @router.post(
        "/_debug/feed-upload-probe",
        summary="Kaspi feed upload probe",
        response_model=KaspiFeedUploadProbeOut,
    )
    async def kaspi_feed_upload_probe(
        request: Request,
        store_name: str = Query(..., min_length=1, alias="store_name"),
        paths: list[str] | None = Query(None),
        base_url: str | None = Query(None),
        current_user: Any = Depends(auth_dependency),
        session: AsyncSession = Depends(get_async_db_dependency),
    ):
        if not is_dev_environment_fn():
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not_found")

        await require_store_admin_company_scoped_fn(current_user)
        token = await kaspi_store_token_model.get_token(session, store_name)
        if not token:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="kaspi_token_not_found")

        base_url = (base_url or "https://kaspi.kz").strip()
        default_paths = [
            "/shop/api/feeds/import",
            "/shop/api/feeds/import/offers",
            "/shop/api/feeds/upload",
        ]
        candidate_paths = paths or default_paths
        now = datetime.utcnow()
        probe_xml = (
            "<?xml version=\"1.0\" encoding=\"UTF-8\"?>"
            f"<kaspi_catalog xmlns=\"{kaspi_ns}\" date=\"{now.strftime('%Y-%m-%dT%H:%M:%S')}\">"
            "<company>Probe</company>"
            f"<merchantid>{store_name}</merchantid>"
            "<offers></offers>"
            "</kaspi_catalog>"
        )
        headers = {
            "X-Auth-Token": token,
            "Content-Type": "application/xml",
            "Accept": "application/json",
            "User-Agent": kaspi_user_agent_fn(),
        }
        request_id = getattr(getattr(request, "state", None), "request_id", None)

        items: list[KaspiFeedUploadProbeItem] = []
        async with httpx.AsyncClient(timeout=fast_probe_timeout, follow_redirects=True) as client:
            for path in candidate_paths:
                normalized = path if path.startswith("/") else f"/{path}"
                url = f"{base_url.rstrip('/')}{normalized}"
                started = time.perf_counter()
                try:
                    resp = await client.post(url, headers=headers, content=probe_xml.encode("utf-8"))
                except httpx.TimeoutException as exc:
                    log_kaspi_probe_error_fn(
                        request_id=request_id,
                        company_id=None,
                        store_name=store_name,
                        method="POST",
                        url=url,
                        exc=exc,
                    )
                    elapsed_ms = int((time.perf_counter() - started) * 1000)
                    items.append(
                        KaspiFeedUploadProbeItem(
                            path=normalized,
                            url=url,
                            ok=False,
                            status_code=None,
                            error_class=type(exc).__name__,
                            message=str(exc),
                            response_snippet=None,
                            location=None,
                            elapsed_ms=elapsed_ms,
                        )
                    )
                    continue
                except httpx.RequestError as exc:
                    log_kaspi_probe_error_fn(
                        request_id=request_id,
                        company_id=None,
                        store_name=store_name,
                        method="POST",
                        url=url,
                        exc=exc,
                    )
                    elapsed_ms = int((time.perf_counter() - started) * 1000)
                    items.append(
                        KaspiFeedUploadProbeItem(
                            path=normalized,
                            url=url,
                            ok=False,
                            status_code=None,
                            error_class=type(exc).__name__,
                            message=str(exc),
                            response_snippet=None,
                            location=None,
                            elapsed_ms=elapsed_ms,
                        )
                    )
                    continue

                elapsed_ms = int((time.perf_counter() - started) * 1000)
                log_kaspi_probe_response_fn(
                    request_id=request_id,
                    company_id=None,
                    store_name=store_name,
                    method="POST",
                    url=url,
                    status_code=resp.status_code,
                    response_text=resp.text,
                    elapsed_ms=elapsed_ms,
                )
                ok = 200 <= resp.status_code < 300
                import_code = None
                try:
                    payload = resp.json()
                    if isinstance(payload, dict):
                        import_code = payload.get("importCode") or payload.get("import_code")
                except Exception:
                    import_code = None
                items.append(
                    KaspiFeedUploadProbeItem(
                        path=normalized,
                        url=url,
                        ok=ok,
                        status_code=resp.status_code,
                        error_class=None if ok else "HTTPStatusError",
                        message=None if ok else "upstream_response",
                        response_snippet=probe_response_snippet_fn(resp.text),
                        location=resp.headers.get("location"),
                        import_code=str(import_code) if import_code else None,
                        elapsed_ms=elapsed_ms,
                    )
                )

        return KaspiFeedUploadProbeOut(ok=bool(items) and all(item.ok for item in items), items=items)
