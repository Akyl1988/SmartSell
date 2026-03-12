from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import FileResponse


def register_kaspi_tooling_routes(
    router: APIRouter,
    *,
    kaspi_offers_seed,
    kaspi_catalog_import_template_csv,
    kaspi_catalog_import_template,
    kaspi_products_list,
    kaspi_offer_seed_out_model,
    kaspi_product_list_out_model,
    legacy_csv_responses,
    template_responses,
) -> None:
    router.add_api_route(
        "/offers/seed",
        kaspi_offers_seed,
        methods=["POST"],
        summary="Dev-only: seed minimal Kaspi offer",
        response_model=kaspi_offer_seed_out_model,
    )
    router.add_api_route(
        "/catalog/import/template.csv",
        kaspi_catalog_import_template_csv,
        methods=["GET"],
        summary="Download catalog import CSV template",
        response_class=FileResponse,
        responses=legacy_csv_responses,
    )
    router.add_api_route(
        "/catalog/template",
        kaspi_catalog_import_template,
        methods=["GET"],
        summary="Download catalog import template",
        response_class=FileResponse,
        responses=template_responses,
    )
    router.add_api_route(
        "/products",
        kaspi_products_list,
        methods=["GET"],
        summary="Получить список каталога Kaspi",
        response_model=kaspi_product_list_out_model,
    )
