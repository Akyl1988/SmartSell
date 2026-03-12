from __future__ import annotations

from fastapi import APIRouter, status


def register_kaspi_core_routes(
    router: APIRouter,
    *,
    connect_store,
    upsert_token,
    list_tokens,
    get_token_by_store_name,
    delete_token,
    kaspi_health,
    kaspi_connect_out_model,
    kaspi_token_out_model,
    kaspi_token_masked_out_model,
) -> None:
    router.add_api_route(
        "/connect",
        connect_store,
        methods=["POST"],
        response_model=kaspi_connect_out_model,
        status_code=status.HTTP_200_OK,
        summary="Kaspi onboarding: connect and configure store (main entry point)",
    )
    router.add_api_route(
        "/tokens",
        upsert_token,
        methods=["POST"],
        response_model=kaspi_token_out_model,
        status_code=status.HTTP_201_CREATED,
        summary="Создать/обновить токен магазина",
    )
    router.add_api_route(
        "/tokens",
        list_tokens,
        methods=["GET"],
        response_model=list[kaspi_token_out_model],
        summary="Список подключённых магазинов",
    )
    router.add_api_route(
        "/tokens/{store_name}",
        get_token_by_store_name,
        methods=["GET"],
        response_model=kaspi_token_masked_out_model,
        summary="Карточка токена (маска + метаданные)",
    )
    router.add_api_route(
        "/tokens/{store_name}",
        delete_token,
        methods=["DELETE"],
        status_code=status.HTTP_204_NO_CONTENT,
        summary="Удалить токен магазина",
    )
    router.add_api_route(
        "/health/{store}",
        kaspi_health,
        methods=["GET"],
        summary="Проверка здоровья Kaspi API для магазина",
    )
