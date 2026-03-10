from __future__ import annotations

from fastapi import APIRouter


def register_kaspi_status_routes(
    router: APIRouter,
    *,
    kaspi_status,
    kaspi_status_out_model,
) -> None:
    router.add_api_route(
        "/status",
        kaspi_status,
        methods=["GET"],
        summary="Статус интеграции Kaspi по компании",
        response_model=kaspi_status_out_model,
    )
