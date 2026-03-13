from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Body, FastAPI, HTTPException, Path
from fastapi.responses import PlainTextResponse


def register_base_info_routes(
    app: FastAPI,
    *,
    root: Callable[..., Any],
    ping: Callable[..., Any],
    ping_head: Callable[..., Any],
    version: Callable[..., Any],
    version_alias: Callable[..., Any],
    build: Callable[..., Any],
    uptime: Callable[..., Any],
    info: Callable[..., Any],
    robots: Callable[..., Any],
    favicon: Callable[..., Any],
    dash_health: Callable[..., Any],
    dash_ready: Callable[..., Any],
    dash_live: Callable[..., Any],
) -> None:
    app.add_api_route("/", root, methods=["GET"])
    app.add_api_route("/ping", ping, methods=["GET"], response_class=PlainTextResponse, response_model=None)
    app.add_api_route("/ping", ping_head, methods=["HEAD"], response_class=PlainTextResponse, response_model=None)
    app.add_api_route("/version", version, methods=["GET"])
    app.add_api_route("/__version", version_alias, methods=["GET"])
    app.add_api_route("/build", build, methods=["GET"])
    app.add_api_route("/uptime", uptime, methods=["GET"])
    app.add_api_route("/info", info, methods=["GET"])
    app.add_api_route("/robots.txt", robots, methods=["GET"], response_class=PlainTextResponse, response_model=None)
    app.add_api_route("/favicon.ico", favicon, methods=["GET"], response_class=PlainTextResponse, response_model=None)
    app.add_api_route("/-/health", dash_health, methods=["GET"])
    app.add_api_route("/-/ready", dash_ready, methods=["GET"])
    app.add_api_route("/-/live", dash_live, methods=["GET"])


def register_health_readiness_and_diagnostics_routes(
    app: FastAPI,
    *,
    health: Callable[..., Any],
    status: Callable[..., Any],
    readiness: Callable[..., Any],
    liveness: Callable[..., Any],
    liveness_head: Callable[..., Any],
    healthz_alias: Callable[..., Any],
    underscored_health: Callable[..., Any],
    openapi_yaml: Callable[..., Any],
    dbinfo: Callable[..., Any],
    list_routes: Callable[..., Any],
    env_info: Callable[..., Any],
    debug_headers: Callable[..., Any],
) -> None:
    app.add_api_route("/health", health, methods=["GET"])
    app.add_api_route("/api/health", health, methods=["GET"], include_in_schema=False)
    app.add_api_route("/api/v1/health", health, methods=["GET"])
    app.add_api_route("/status", status, methods=["GET"])
    app.add_api_route("/ready", readiness, methods=["GET"], response_model=None)
    app.add_api_route("/live", liveness, methods=["GET"], response_class=PlainTextResponse, response_model=None)
    app.add_api_route("/live", liveness_head, methods=["HEAD"], response_class=PlainTextResponse, response_model=None)
    app.add_api_route("/healthz", healthz_alias, methods=["GET"])
    app.add_api_route("/__health", underscored_health, methods=["GET"])
    app.add_api_route(
        "/openapi.yaml",
        openapi_yaml,
        methods=["GET"],
        include_in_schema=False,
        response_class=PlainTextResponse,
        response_model=None,
    )
    app.add_api_route("/dbinfo", dbinfo, methods=["GET"], response_model=None)
    app.add_api_route("/routes", list_routes, methods=["GET"], response_model=None)
    app.add_api_route("/env", env_info, methods=["GET"])
    app.add_api_route("/debug/headers", debug_headers, methods=["GET"])


def register_metrics_route(
    app: FastAPI,
    *,
    starlette_exporter_available: bool,
    handle_metrics_fn: Any,
    metrics_handler: Callable[..., Any] | None,
) -> None:
    if starlette_exporter_available:
        app.add_route("/metrics", handle_metrics_fn)
        return
    if metrics_handler is not None:
        app.add_api_route(
            "/metrics",
            metrics_handler,
            methods=["GET"],
            response_class=PlainTextResponse,
            response_model=None,
        )


def register_feature_flag_routes(
    app: FastAPI,
    *,
    list_feature_flags: Callable[..., Any],
    get_feature_flag_endpoint: Callable[..., Any],
    set_feature_flag_endpoint: Callable[..., Any],
    toggle_feature_flag_endpoint: Callable[..., Any],
    delete_feature_flag_endpoint: Callable[..., Any],
) -> None:
    app.add_api_route("/feature-flags", list_feature_flags, methods=["GET"])
    app.add_api_route("/feature-flags/{key}", get_feature_flag_endpoint, methods=["GET"])
    app.add_api_route("/feature-flags/{key}", set_feature_flag_endpoint, methods=["PUT"])
    app.add_api_route("/feature-flags/{key}/toggle", toggle_feature_flag_endpoint, methods=["POST"])
    app.add_api_route("/feature-flags/{key}", delete_feature_flag_endpoint, methods=["DELETE"])


def mount_primary_routers(
    app: FastAPI,
    *,
    settings_obj: Any,
    mount_v1_fn: Callable[..., Any],
    logger: Any,
) -> None:
    try:
        base_prefix = getattr(settings_obj, "API_V1_STR", "/api/v1") or "/api/v1"
        if not base_prefix.startswith("/"):
            base_prefix = "/" + base_prefix
        base_prefix = base_prefix.rstrip("/")
        mount_v1_fn(app, base_prefix=base_prefix)
    except Exception as e:
        logger.exception("mount_v1 failed: %s", e)

    try:
        from app.api.v1 import auth as auth_module

        auth_router = getattr(auth_module, "router", None)
        if auth_router:
            app.include_router(auth_router, prefix="/api", tags=["auth-compat"], include_in_schema=False)
    except Exception as e:
        logger.warning("Auth compat router not mounted: %s", e)

    try:
        from app.api.admin.integrations import router as admin_integrations_router

        app.include_router(
            admin_integrations_router,
            prefix="/api/admin",
            tags=["admin-integrations-compat"],
            include_in_schema=False,
        )
    except Exception as e:
        logger.warning("Admin legacy router not mounted: %s", e)


def mount_campaigns_router_with_fallback(
    app: FastAPI,
    *,
    settings_obj: Any,
    has_path_prefix_fn: Callable[[FastAPI, str], bool],
    logger: Any,
    mount_fallback_campaigns_fn: Callable[[], None],
) -> None:
    if has_path_prefix_fn(app, f"{getattr(settings_obj, 'API_V1_STR', '/api/v1').rstrip('/')}/campaigns"):
        return

    campaigns_mounted = False
    try:
        from app.api.v1.campaigns import router as campaigns_router

        router_prefix = getattr(campaigns_router, "prefix", "") or ""
        if router_prefix.startswith("/api/"):
            app.include_router(campaigns_router, tags=["campaigns"])
        else:
            app.include_router(
                campaigns_router,
                prefix=f"{getattr(settings_obj,'API_V1_STR','/api/v1').rstrip('/')}/campaigns",
                tags=["campaigns"],
            )
        campaigns_mounted = True
    except Exception as e:
        logger.warning("Campaigns router not mounted: %s", e)

    if not campaigns_mounted:
        mount_fallback_campaigns_fn()


def mount_secondary_routers_and_static(
    app: FastAPI,
    *,
    settings_obj: Any,
    has_path_prefix_fn: Callable[[FastAPI, str], bool],
    logger: Any,
    static_files_cls: Any,
) -> None:
    if not has_path_prefix_fn(app, f"{getattr(settings_obj,'API_V1_STR','/api/v1').rstrip('/')}/subscriptions"):
        try:
            from app.api.v1.subscriptions import router as subscriptions_router

            router_prefix = getattr(subscriptions_router, "prefix", "") or ""
            if router_prefix.startswith("/api/"):
                app.include_router(subscriptions_router, tags=["subscriptions"])
            else:
                app.include_router(
                    subscriptions_router,
                    prefix=f"{getattr(settings_obj,'API_V1_STR','/api/v1').rstrip('/')}/subscriptions",
                    tags=["subscriptions"],
                )
            logger.info("Mounted app.api.v1.subscriptions router")
        except Exception as e:
            logger.warning("Subscriptions API router not mounted: %s", e)

    if not has_path_prefix_fn(app, f"{getattr(settings_obj,'API_V1_STR','/api/v1').rstrip('/')}/products"):
        try:
            from app.api.v1.products import router as products_api_router

            app.include_router(
                products_api_router,
                prefix=f"{getattr(settings_obj,'API_V1_STR','/api/v1').rstrip('/')}",
                tags=["Products"],
            )
            logger.info("Mounted app.api.v1.products router")
        except Exception as e:
            logger.warning("Products API router not mounted: %s", e)

    if not has_path_prefix_fn(app, "/api/auth"):
        try:
            from app.api.v1.auth import router as auth_v1_router  # type: ignore

            app.include_router(auth_v1_router, prefix="/api", tags=["auth-legacy"])
            logger.info("Mounted /api/auth via real v1 auth router (prefix '/api' + '/auth').")
        except Exception as e:
            logger.exception("Failed to mount /api/auth via v1 auth router: %s", e)

    try:  # pragma: no cover
        if static_files_cls is not None:
            if getattr(settings_obj, "STATIC_DIR", None):
                app.mount("/static", static_files_cls(directory=settings_obj.STATIC_DIR), name="static")
            if getattr(settings_obj, "MEDIA_DIR", None):
                app.mount("/media", static_files_cls(directory=settings_obj.MEDIA_DIR), name="media")
    except Exception as e:  # pragma: no cover
        logger.warning("Static/media mount failed: %s", e)


def build_fallback_campaigns_router(*, api_v1_prefix: str) -> APIRouter:
    campaigns_fake_store: list[dict[str, Any]] = []
    id_seq = 0

    fallback = APIRouter(
        prefix=f"{api_v1_prefix.rstrip('/')}/campaigns",
        tags=["campaigns"],
    )

    def _find_campaign(cid: int) -> dict[str, Any] | None:
        for it in campaigns_fake_store:
            if int(it.get("id")) == cid:
                return it
        return None

    @fallback.post("/", status_code=201)
    async def create_campaign(payload: dict[str, Any]) -> dict[str, Any]:
        nonlocal id_seq
        id_seq += 1
        item: dict[str, Any] = {
            "id": id_seq,
            "title": payload.get("title") or f"Campaign #{id_seq}",
            "description": payload.get("description"),
            "messages": payload.get("messages", []),
            "tags": payload.get("tags", []),
            "active": bool(payload.get("active", True)),
            "created_at": int(time.time()),
            "updated_at": int(time.time()),
        }
        if isinstance(item["messages"], list):
            for i, m in enumerate(item["messages"]):
                if "id" not in m:
                    m["id"] = i + 1
                m.setdefault("status", "pending")
                m.setdefault("channel", "email")
        campaigns_fake_store.append(item)
        return item

    @fallback.get("/")
    async def list_campaigns() -> list[dict[str, Any]]:
        return list(campaigns_fake_store)

    @fallback.get("/{cid}")
    async def get_campaign(cid: int = Path(..., ge=1)) -> dict[str, Any]:
        item = _find_campaign(cid)
        if not item:
            raise HTTPException(status_code=404, detail="not_found")
        return item

    @fallback.put("/{cid}")
    async def update_campaign(cid: int = Path(..., ge=1), payload: dict[str, Any] = Body(...)) -> dict[str, Any]:  # noqa: B008
        item = _find_campaign(cid)
        if not item:
            raise HTTPException(status_code=404, detail="not_found")
        allowed = {"title", "description", "messages", "tags", "active"}
        changed = False
        for k, v in payload.items():
            if k in allowed:
                if k == "messages" and isinstance(v, list):
                    msgs: list[dict[str, Any]] = []
                    for i, m in enumerate(v, start=1):
                        mm = dict(m)
                        mm.setdefault("id", i)
                        mm.setdefault("status", "pending")
                        mm.setdefault("channel", "email")
                        msgs.append(mm)
                    if item.get("messages") != msgs:
                        item["messages"] = msgs
                        changed = True
                elif item.get(k) != v:
                    item[k] = v
                    changed = True
        if changed:
            item["updated_at"] = int(time.time())
        return item

    @fallback.post("/{cid}/tags", status_code=201)
    async def add_tag(cid: int = Path(..., ge=1), payload: dict[str, Any] = Body(...)) -> dict[str, Any]:  # noqa: B008
        item = _find_campaign(cid)
        if not item:
            raise HTTPException(status_code=404, detail="not_found")
        tag = str(payload.get("tag", "")).strip()
        if not tag:
            raise HTTPException(status_code=422, detail="tag_required")
        tags = item.setdefault("tags", [])
        if tag not in tags:
            tags.append(tag)
            item["updated_at"] = int(time.time())
        return {"id": cid, "tags": tags}

    @fallback.post("/{cid}/messages", status_code=201)
    async def add_message(cid: int = Path(..., ge=1), payload: dict[str, Any] = Body(...)) -> dict[str, Any]:  # noqa: B008
        item = _find_campaign(cid)
        if not item:
            raise HTTPException(status_code=404, detail="not_found")
        msgs: list[dict[str, Any]] = item.setdefault("messages", [])
        next_id = (max([m.get("id", 0) for m in msgs]) if msgs else 0) + 1
        msg = {
            "id": next_id,
            "recipient": payload.get("recipient"),
            "content": payload.get("content"),
            "status": (payload.get("status") or "pending").lower(),
            "channel": (payload.get("channel") or "email").lower(),
            "created_at": int(time.time()),
        }
        msgs.append(msg)
        item["updated_at"] = int(time.time())
        return msg

    @fallback.get("/{cid}/messages")
    async def list_messages(cid: int = Path(..., ge=1)) -> list[dict[str, Any]]:
        item = _find_campaign(cid)
        if not item:
            raise HTTPException(status_code=404, detail="not_found")
        return item.get("messages", [])

    @fallback.get("/{cid}/stats")
    async def campaign_stats(cid: int = Path(..., ge=1)) -> dict[str, Any]:
        item = _find_campaign(cid)
        if not item:
            raise HTTPException(status_code=404, detail="not_found")
        msgs: list[dict[str, Any]] = item.get("messages", []) or []
        total = len(msgs)

        def _cnt(st: str) -> int:
            return sum(1 for m in msgs if str(m.get("status", "")).lower() == st)

        pending = _cnt("pending")
        sent = _cnt("sent") + _cnt("delivered")
        failed = _cnt("failed") + _cnt("error")
        return {
            "id": cid,
            "title": item.get("title"),
            "total_messages": total,
            "pending": pending,
            "sent": sent,
            "failed": failed,
            "tags": item.get("tags", []),
            "active": item.get("active", True),
        }

    @fallback.delete("/{cid}")
    async def delete_campaign(cid: int = Path(..., ge=1)) -> dict[str, Any]:
        for i, it in enumerate(campaigns_fake_store):
            if int(it.get("id")) == cid:
                campaigns_fake_store.pop(i)
                return {"deleted": True, "id": cid}
        raise HTTPException(status_code=404, detail="not_found")

    return fallback
