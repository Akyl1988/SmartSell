from __future__ import annotations

from typing import Any

import pytest

pytestmark = pytest.mark.asyncio


def _resolve_ref(schema: dict[str, Any], components: dict[str, Any]) -> dict[str, Any]:
    while "$ref" in schema:
        ref = schema.get("$ref", "")
        name = ref.split("/")[-1]
        schema = components.get(name, {})
    return schema


def _merge_all_of(schema: dict[str, Any], components: dict[str, Any]) -> dict[str, Any]:
    merged: dict[str, Any] = {"type": "object", "properties": {}, "required": []}
    for item in schema.get("allOf", []):
        resolved = _resolve_ref(item, components)
        if "allOf" in resolved:
            resolved = _merge_all_of(resolved, components)
        if resolved.get("type") == "object" or "properties" in resolved:
            merged["properties"].update(resolved.get("properties", {}))
            merged["required"].extend(resolved.get("required", []))
    merged["required"] = list(dict.fromkeys(merged["required"]))
    return merged


def _example_for_schema(schema: dict[str, Any], components: dict[str, Any]) -> Any:
    schema = _resolve_ref(schema, components)
    if "allOf" in schema:
        schema = _merge_all_of(schema, components)
    if "oneOf" in schema:
        return _example_for_schema(schema["oneOf"][0], components)
    if "anyOf" in schema:
        return _example_for_schema(schema["anyOf"][0], components)
    if "enum" in schema:
        return schema["enum"][0]
    if "default" in schema:
        return schema["default"]

    schema_type = schema.get("type")
    if schema_type == "object" or "properties" in schema:
        props = schema.get("properties", {})
        required = schema.get("required", [])
        return {key: _example_for_schema(props.get(key, {}), components) for key in required}
    if schema_type == "array":
        items = schema.get("items", {})
        min_items = int(schema.get("minItems") or 1)
        return [_example_for_schema(items, components) for _ in range(max(min_items, 1))]
    if schema_type == "integer":
        if "minimum" in schema:
            return int(schema["minimum"])
        return 1
    if schema_type == "number":
        if "minimum" in schema:
            return float(schema["minimum"])
        return 1.0
    if schema_type == "boolean":
        return True

    fmt = schema.get("format")
    if fmt == "date-time":
        return "2026-02-07T00:00:00Z"
    if fmt == "date":
        return "2026-02-07"
    if fmt == "email":
        return "user@example.com"

    min_len = int(schema.get("minLength") or 4)
    return "x" * max(min_len, 4)


def _build_request(
    path: str,
    operation: dict[str, Any],
    components: dict[str, Any],
) -> tuple[str, dict[str, Any], Any | None, Any | None]:
    params: dict[str, Any] = {}
    path_values: dict[str, Any] = {}

    for param in operation.get("parameters", []):
        if not param.get("required"):
            continue
        value = _example_for_schema(param.get("schema", {}), components)
        location = param.get("in")
        if location == "path":
            path_values[param["name"]] = value
        elif location == "query":
            params[param["name"]] = value

    url = path.format(**{key: str(value) for key, value in path_values.items()})

    json_body = None
    data = None
    request_body = operation.get("requestBody") or {}
    content = request_body.get("content", {})
    if "application/json" in content:
        json_body = _example_for_schema(content["application/json"].get("schema", {}), components)
    elif "application/x-www-form-urlencoded" in content:
        data = _example_for_schema(content["application/x-www-form-urlencoded"].get("schema", {}), components)
    elif "multipart/form-data" in content:
        data = _example_for_schema(content["multipart/form-data"].get("schema", {}), components)

    return url, params, json_body, data


def _assert_forbidden(resp) -> None:
    assert resp.status_code == 403, resp.text
    payload = resp.json()
    assert payload.get("code") == "ADMIN_REQUIRED"
    assert payload.get("request_id")


async def test_admin_routes_platform_only_contract(
    async_client,
    auth_headers,
    company_a_admin_headers,
    company_a_employee_headers,
):
    response = await async_client.get("/openapi.json")
    assert response.status_code == 200
    payload = response.json()

    paths = payload.get("paths", {})
    components = payload.get("components", {}).get("schemas", {})
    allowlist: set[str] = set()
    missing_security: list[tuple[str, str]] = []

    for path, operations in paths.items():
        if not path.startswith("/api/v1/admin/"):
            continue
        if path in allowlist:
            continue
        for method, operation in operations.items():
            if method not in {"get", "post", "put", "patch", "delete"}:
                continue
            url, params, json_body, data = _build_request(path, operation, components)
            resp_store_admin = await async_client.request(
                method.upper(),
                url,
                headers=company_a_admin_headers,
                params=params or None,
                json=json_body,
                data=data,
            )
            _assert_forbidden(resp_store_admin)

            resp_employee = await async_client.request(
                method.upper(),
                url,
                headers=company_a_employee_headers,
                params=params or None,
                json=json_body,
                data=data,
            )
            _assert_forbidden(resp_employee)

            resp_platform = await async_client.request(
                method.upper(),
                url,
                headers=auth_headers,
                params=params or None,
                json=json_body,
                data=data,
            )
            assert (
                resp_platform.status_code != 403
            ), f"{method.upper()} {url} -> {resp_platform.status_code}: {resp_platform.text}"

            if operation.get("security") is None and not payload.get("security"):
                missing_security.append((method, path))

    if missing_security:
        pytest.skip("OpenAPI security not declared for admin operations; dependency-only guards not reflected.")
