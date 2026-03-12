from __future__ import annotations

from fastapi import APIRouter


def register_kaspi_orders_routes(
    router: APIRouter,
    *,
    kaspi_orders_list,
    kaspi_order_detail,
    kaspi_order_accept,
    kaspi_order_cancel,
    kaspi_orders,
    kaspi_import,
    kaspi_import_status,
    kaspi_orders_list_out_model,
    kaspi_order_detail_out_model,
    kaspi_order_action_out_model,
) -> None:
    router.add_api_route(
        "/orders",
        kaspi_orders_list,
        methods=["GET"],
        response_model=kaspi_orders_list_out_model,
        summary="Kaspi orders list (local)",
    )
    router.add_api_route(
        "/orders/{order_id}",
        kaspi_order_detail,
        methods=["GET"],
        response_model=kaspi_order_detail_out_model,
        summary="Kaspi order detail (local)",
    )
    router.add_api_route(
        "/orders/{external_id}/accept",
        kaspi_order_accept,
        methods=["POST"],
        summary="Accept Kaspi order",
        response_model=kaspi_order_action_out_model,
    )
    router.add_api_route(
        "/orders/{external_id}/cancel",
        kaspi_order_cancel,
        methods=["POST"],
        summary="Cancel Kaspi order",
        response_model=kaspi_order_action_out_model,
    )
    router.add_api_route(
        "/orders",
        kaspi_orders,
        methods=["POST"],
        summary="Получить заказы из Kaspi (проксирование через адаптер)",
    )
    router.add_api_route(
        "/import",
        kaspi_import,
        methods=["POST"],
        summary="Запустить импорт офферов (фид) в Kaspi",
    )
    router.add_api_route(
        "/import/status",
        kaspi_import_status,
        methods=["POST"],
        summary="Проверить статус импорта офферов в Kaspi",
    )
