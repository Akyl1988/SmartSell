from __future__ import annotations

from fastapi import APIRouter, Response


def register_kaspi_public_routes(
    router: APIRouter,
    public_router: APIRouter,
    *,
    kaspi_feed_public_token_create,
    kaspi_feed_public_tokens_list,
    kaspi_feed_public_token_revoke,
    kaspi_public_price_list,
    kaspi_public_offers_feed,
    kaspi_feed_public_token_out_model,
    kaspi_feed_public_token_list_out_model,
) -> None:
    router.add_api_route(
        "/feed/public-tokens",
        kaspi_feed_public_token_create,
        methods=["POST"],
        summary="Create public feed token",
        response_model=kaspi_feed_public_token_out_model,
    )
    router.add_api_route(
        "/feed/public-tokens",
        kaspi_feed_public_tokens_list,
        methods=["GET"],
        summary="List public feed tokens",
        response_model=kaspi_feed_public_token_list_out_model,
    )
    router.add_api_route(
        "/feed/public-tokens/{token_id}/revoke",
        kaspi_feed_public_token_revoke,
        methods=["POST"],
        summary="Revoke public feed token",
        response_model=kaspi_feed_public_token_out_model,
    )
    public_router.add_api_route(
        "/public/kaspi/price-list/{token}.xml",
        kaspi_public_price_list,
        methods=["GET"],
        summary="Public Kaspi price list feed",
        response_class=Response,
        include_in_schema=False,
    )
    router.add_api_route(
        "/feed/public/offers.xml",
        kaspi_public_offers_feed,
        methods=["GET"],
        summary="Public Kaspi offers feed",
        response_class=Response,
    )
