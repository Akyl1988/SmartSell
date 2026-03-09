from __future__ import annotations

from fastapi import APIRouter, Response


def register_kaspi_feed_routes_phase_one(
    router: APIRouter,
    *,
    kaspi_feed_export_create,
    kaspi_feed_exports_list,
    kaspi_offers_feed_upload,
    kaspi_feed_upload_create,
    kaspi_feed_uploads_list,
    kaspi_feed_upload_get,
    kaspi_feed_upload_refresh,
    kaspi_feed_upload_refresh_compat,
    kaspi_feed_upload_publish,
    kaspi_feed_export_detail,
    kaspi_feed_export_download,
    kaspi_feed_export_out_model,
    kaspi_offers_feed_upload_out_model,
    kaspi_feed_upload_record_out_model,
) -> None:
    router.add_api_route(
        "/feed/exports",
        kaspi_feed_export_create,
        methods=["POST"],
        summary="Generate Kaspi offers feed export",
        response_model=kaspi_feed_export_out_model,
    )
    router.add_api_route(
        "/feed/exports",
        kaspi_feed_exports_list,
        methods=["GET"],
        summary="List Kaspi feed exports",
        response_model=list[kaspi_feed_export_out_model],
    )
    router.add_api_route(
        "/offers/feed/upload",
        kaspi_offers_feed_upload,
        methods=["POST"],
        summary="Upload Kaspi offers feed (XML)",
        response_model=kaspi_offers_feed_upload_out_model,
    )
    router.add_api_route(
        "/feed/uploads",
        kaspi_feed_upload_create,
        methods=["POST"],
        summary="Upload Kaspi offers feed",
        response_model=kaspi_feed_upload_record_out_model,
    )
    router.add_api_route(
        "/feed/uploads",
        kaspi_feed_uploads_list,
        methods=["GET"],
        summary="List Kaspi feed uploads",
        response_model=list[kaspi_feed_upload_record_out_model],
    )
    router.add_api_route(
        "/feed/uploads/{upload_id}",
        kaspi_feed_upload_get,
        methods=["GET"],
        summary="Get Kaspi feed upload",
        response_model=kaspi_feed_upload_record_out_model,
    )
    router.add_api_route(
        "/feed/uploads/{upload_id}/refresh",
        kaspi_feed_upload_refresh,
        methods=["POST"],
        summary="Refresh Kaspi feed upload status",
        response_model=kaspi_feed_upload_record_out_model,
    )
    router.add_api_route(
        "/feed/uploads/{upload_id}/refresh-status",
        kaspi_feed_upload_refresh_compat,
        methods=["POST"],
        summary="Refresh Kaspi feed upload status (deprecated)",
        response_model=kaspi_feed_upload_record_out_model,
    )
    router.add_api_route(
        "/feed/uploads/{upload_id}/publish",
        kaspi_feed_upload_publish,
        methods=["POST"],
        summary="Publish Kaspi feed upload",
        response_model=kaspi_feed_upload_record_out_model,
    )
    router.add_api_route(
        "/feed/exports/{export_id}",
        kaspi_feed_export_detail,
        methods=["GET"],
        summary="Get Kaspi feed export details",
        response_model=kaspi_feed_export_out_model,
    )
    router.add_api_route(
        "/feed/exports/{export_id}/download",
        kaspi_feed_export_download,
        methods=["GET"],
        summary="Download Kaspi feed export XML",
        response_class=Response,
    )


def register_kaspi_feed_routes_phase_two(
    router: APIRouter,
    *,
    kaspi_feed_generate_products,
    kaspi_feed_upload,
    kaspi_feeds_list,
    kaspi_feed_get,
    kaspi_feed_get_payload,
    kaspi_feed_generate_out_model,
    kaspi_feed_upload_record_out_model,
    kaspi_feed_list_out_model,
    kaspi_feed_export_out_model,
) -> None:
    router.add_api_route(
        "/feeds/products/generate",
        kaspi_feed_generate_products,
        methods=["POST"],
        summary="Сгенерировать фид продуктов для Kaspi",
        response_model=kaspi_feed_generate_out_model,
    )
    router.add_api_route(
        "/feeds/{export_id}/upload",
        kaspi_feed_upload,
        methods=["POST"],
        summary="Загрузить фид на Kaspi",
        response_model=kaspi_feed_upload_record_out_model,
    )
    router.add_api_route(
        "/feeds",
        kaspi_feeds_list,
        methods=["GET"],
        summary="Получить список фидов",
        response_model=kaspi_feed_list_out_model,
    )
    router.add_api_route(
        "/feeds/{export_id}",
        kaspi_feed_get,
        methods=["GET"],
        summary="Получить метаданные фида",
        response_model=kaspi_feed_export_out_model,
    )
    router.add_api_route(
        "/feeds/{export_id}/payload",
        kaspi_feed_get_payload,
        methods=["GET"],
        summary="Получить XML фида",
        response_class=Response,
    )
