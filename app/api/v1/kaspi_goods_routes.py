from __future__ import annotations

from fastapi import APIRouter


def register_kaspi_goods_routes(
    router: APIRouter,
    *,
    kaspi_goods_schema,
    kaspi_goods_categories,
    kaspi_goods_attributes,
    kaspi_goods_attribute_values,
    kaspi_goods_import_upload,
    kaspi_goods_import_status_by_code,
    kaspi_goods_import,
    kaspi_goods_import_status,
    kaspi_goods_import_result,
    kaspi_goods_import_create,
    kaspi_goods_import_list,
    kaspi_goods_import_get,
    kaspi_goods_import_refresh,
    kaspi_sync_now,
    kaspi_token_health,
    kaspi_token_selftest,
    kaspi_catalog_import,
    kaspi_goods_upload_out_model,
    kaspi_goods_status_out_model,
    kaspi_goods_import_out_model,
    kaspi_goods_result_out_model,
    kaspi_goods_import_record_out_model,
    kaspi_sync_now_out_model,
    kaspi_token_health_out_model,
    kaspi_token_selftest_out_model,
    kaspi_catalog_import_out_model,
) -> None:
    router.add_api_route(
        "/goods/schema",
        kaspi_goods_schema,
        methods=["GET"],
        summary="Kaspi goods import schema",
    )
    router.add_api_route(
        "/goods/categories",
        kaspi_goods_categories,
        methods=["GET"],
        summary="Kaspi goods categories",
    )
    router.add_api_route(
        "/goods/attributes",
        kaspi_goods_attributes,
        methods=["GET"],
        summary="Kaspi goods attributes for category",
    )
    router.add_api_route(
        "/goods/attribute-values",
        kaspi_goods_attribute_values,
        methods=["GET"],
        summary="Kaspi goods attribute values",
    )
    router.add_api_route(
        "/goods/import/upload",
        kaspi_goods_import_upload,
        methods=["POST"],
        summary="Kaspi goods import upload (file)",
        response_model=kaspi_goods_upload_out_model,
    )
    router.add_api_route(
        "/goods/import/status",
        kaspi_goods_import_status_by_code,
        methods=["GET"],
        summary="Kaspi goods import status (by importCode)",
        response_model=kaspi_goods_status_out_model,
    )
    router.add_api_route(
        "/goods/import",
        kaspi_goods_import,
        methods=["POST"],
        summary="Kaspi goods import",
        response_model=kaspi_goods_import_out_model,
    )
    router.add_api_route(
        "/goods/import/{code}",
        kaspi_goods_import_status,
        methods=["GET"],
        summary="Kaspi goods import status",
        response_model=kaspi_goods_status_out_model,
    )
    router.add_api_route(
        "/goods/import/{code}/result",
        kaspi_goods_import_result,
        methods=["GET"],
        summary="Kaspi goods import result",
        response_model=kaspi_goods_result_out_model,
    )
    router.add_api_route(
        "/goods/imports",
        kaspi_goods_import_create,
        methods=["POST"],
        summary="Kaspi goods import (stored)",
        response_model=kaspi_goods_import_record_out_model,
    )
    router.add_api_route(
        "/goods/imports",
        kaspi_goods_import_list,
        methods=["GET"],
        summary="List Kaspi goods imports",
        response_model=list[kaspi_goods_import_record_out_model],
    )
    router.add_api_route(
        "/goods/imports/{import_id}",
        kaspi_goods_import_get,
        methods=["GET"],
        summary="Get Kaspi goods import",
        response_model=kaspi_goods_import_record_out_model,
    )
    router.add_api_route(
        "/goods/imports/{import_id}/refresh",
        kaspi_goods_import_refresh,
        methods=["POST"],
        summary="Refresh Kaspi goods import",
        response_model=kaspi_goods_import_record_out_model,
    )
    router.add_api_route(
        "/sync/now",
        kaspi_sync_now,
        methods=["POST"],
        summary="Kaspi sync now",
        response_model=kaspi_sync_now_out_model,
    )
    router.add_api_route(
        "/token/health",
        kaspi_token_health,
        methods=["GET"],
        summary="Kaspi token health",
        response_model=kaspi_token_health_out_model,
    )
    router.add_api_route(
        "/token/selftest",
        kaspi_token_selftest,
        methods=["GET"],
        summary="Kaspi token self-test",
        response_model=kaspi_token_selftest_out_model,
    )
    router.add_api_route(
        "/catalog/import",
        kaspi_catalog_import,
        methods=["POST"],
        summary="Kaspi catalog import (CSV/XLSX/JSON)",
        response_model=kaspi_catalog_import_out_model,
    )
