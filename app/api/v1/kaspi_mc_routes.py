from __future__ import annotations

from fastapi import APIRouter


def register_kaspi_mc_routes(
    router: APIRouter,
    *,
    kaspi_mc_session_upsert,
    kaspi_mc_session_status,
    kaspi_catalog_sync_mc,
    kaspi_catalog_import_batches,
    kaspi_catalog_import_batch_detail,
    kaspi_catalog_import_batch_errors,
    kaspi_offers_list,
    kaspi_mc_session_out_model,
    kaspi_mc_session_list_out_model,
    kaspi_mc_sync_out_model,
    kaspi_catalog_import_batch_out_model,
    kaspi_catalog_import_batch_detail_out_model,
    kaspi_catalog_import_error_out_model,
    kaspi_offer_list_out_model,
) -> None:
    router.add_api_route(
        "/mc/session",
        kaspi_mc_session_upsert,
        methods=["POST"],
        summary="Upsert Kaspi MC session cookies",
        response_model=kaspi_mc_session_out_model,
    )
    router.add_api_route(
        "/mc/session",
        kaspi_mc_session_status,
        methods=["GET"],
        summary="Kaspi MC session status",
        response_model=kaspi_mc_session_list_out_model,
    )
    router.add_api_route(
        "/catalog/sync/mc",
        kaspi_catalog_sync_mc,
        methods=["POST"],
        summary="Kaspi MC catalog sync",
        response_model=kaspi_mc_sync_out_model,
    )
    router.add_api_route(
        "/catalog/import/batches",
        kaspi_catalog_import_batches,
        methods=["GET"],
        summary="List catalog import batches (newest first)",
        response_model=list[kaspi_catalog_import_batch_out_model],
    )
    router.add_api_route(
        "/catalog/import/batches/{batch_id}",
        kaspi_catalog_import_batch_detail,
        methods=["GET"],
        summary="Get catalog import batch detail",
        response_model=kaspi_catalog_import_batch_detail_out_model,
    )
    router.add_api_route(
        "/catalog/import/batches/{batch_id}/errors",
        kaspi_catalog_import_batch_errors,
        methods=["GET"],
        summary="List catalog import errors",
        response_model=list[kaspi_catalog_import_error_out_model],
    )
    router.add_api_route(
        "/offers",
        kaspi_offers_list,
        methods=["GET"],
        summary="List Kaspi offers",
        response_model=kaspi_offer_list_out_model,
    )
