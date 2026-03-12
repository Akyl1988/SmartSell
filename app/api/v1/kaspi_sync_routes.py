from __future__ import annotations

from fastapi import APIRouter, Response


def register_kaspi_sync_routes(
    router: APIRouter,
    *,
    kaspi_orders_sync,
    kaspi_generate_feed,
    kaspi_availability_sync_one,
    kaspi_availability_bulk,
    kaspi_orders_sync_state,
    kaspi_orders_sync_ops,
    kaspi_catalog_items,
    kaspi_sync_state_out_model,
    kaspi_sync_ops_out_model,
    kaspi_catalog_items_out_model,
) -> None:
    router.add_api_route(
        "/orders/sync",
        kaspi_orders_sync,
        methods=["POST"],
        summary="Синхронизировать последние заказы Kaspi в локальную БД",
    )
    router.add_api_route(
        "/feed",
        kaspi_generate_feed,
        methods=["GET"],
        summary="Сгенерировать XML-фид активных товаров компании",
        response_class=Response,
    )
    router.add_api_route(
        "/availability/sync",
        kaspi_availability_sync_one,
        methods=["POST"],
        summary="Синхронизировать доступность (stock) одного товара в Kaspi",
    )
    router.add_api_route(
        "/availability/bulk",
        kaspi_availability_bulk,
        methods=["POST"],
        summary="Массовая синхронизация доступности активных товаров компании",
    )
    router.add_api_route(
        "/orders/sync/state",
        kaspi_orders_sync_state,
        methods=["GET"],
        summary="Текущее состояние синхронизации заказов Kaspi",
        response_model=kaspi_sync_state_out_model,
    )
    router.add_api_route(
        "/orders/sync/ops",
        kaspi_orders_sync_ops,
        methods=["GET"],
        summary="Операционный статус синхронизации заказов Kaspi (state + lock)",
        response_model=kaspi_sync_ops_out_model,
    )
    router.add_api_route(
        "/catalog/items",
        kaspi_catalog_items,
        methods=["GET"],
        summary="Kaspi catalog items derived from orders",
        response_model=kaspi_catalog_items_out_model,
    )
