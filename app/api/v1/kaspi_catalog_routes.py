from __future__ import annotations

from fastapi import APIRouter


def register_kaspi_catalog_routes(
    router: APIRouter,
    *,
    kaspi_offers_rebuild,
    kaspi_offers_import,
    kaspi_offers_preview,
    kaspi_products_sync,
    kaspi_products_import_start,
    kaspi_products_import_upload,
    kaspi_products_import_poll,
    kaspi_products_import_status,
    kaspi_products_import_schema,
    kaspi_products_import_result,
    kaspi_offers_rebuild_out_model,
    kaspi_offers_import_out_model,
    kaspi_offers_preview_out_model,
    kaspi_catalog_pull_unsupported_out_model,
    kaspi_import_run_out_model,
    kaspi_import_run_poll_out_model,
    kaspi_goods_status_out_model,
    kaspi_goods_result_out_model,
) -> None:
    router.add_api_route(
        "/offers/rebuild",
        kaspi_offers_rebuild,
        methods=["POST"],
        summary="Rebuild Kaspi offers from products",
        response_model=kaspi_offers_rebuild_out_model,
    )
    router.add_api_route(
        "/offers/import",
        kaspi_offers_import,
        methods=["POST"],
        summary="Import Kaspi offers from file",
        response_model=kaspi_offers_import_out_model,
    )
    router.add_api_route(
        "/offers/preview",
        kaspi_offers_preview,
        methods=["GET"],
        summary="Preview Kaspi offers payload",
        response_model=kaspi_offers_preview_out_model,
    )
    router.add_api_route(
        "/products/sync",
        kaspi_products_sync,
        methods=["POST"],
        summary="Синхронизировать каталог Kaspi в локальную БД",
        response_model=kaspi_catalog_pull_unsupported_out_model,
    )
    router.add_api_route(
        "/products/import/start",
        kaspi_products_import_start,
        methods=["POST"],
        summary="Start Kaspi products import run",
        response_model=kaspi_import_run_out_model,
    )
    router.add_api_route(
        "/products/import/upload",
        kaspi_products_import_upload,
        methods=["POST"],
        summary="Upload offers payload to Kaspi",
        response_model=kaspi_import_run_out_model,
    )
    router.add_api_route(
        "/products/import/poll",
        kaspi_products_import_poll,
        methods=["POST"],
        summary="Poll Kaspi import status/result",
        response_model=kaspi_import_run_poll_out_model,
    )
    router.add_api_route(
        "/products/import",
        kaspi_products_import_status,
        methods=["GET"],
        summary="Kaspi products import status",
        response_model=kaspi_goods_status_out_model,
    )
    router.add_api_route(
        "/products/import/schema",
        kaspi_products_import_schema,
        methods=["GET"],
        summary="Kaspi products import schema",
    )
    router.add_api_route(
        "/products/import/result",
        kaspi_products_import_result,
        methods=["GET"],
        summary="Kaspi products import result",
        response_model=kaspi_goods_result_out_model,
    )
