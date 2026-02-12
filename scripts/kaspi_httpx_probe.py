from __future__ import annotations

import argparse
import asyncio
import time
from datetime import datetime, timedelta, timezone

import httpx


def _build_headers(token: str) -> dict[str, str]:
    return {
        "X-Auth-Token": token,
        "Accept": "application/vnd.api+json",
        "Content-Type": "application/vnd.api+json",
        "User-Agent": "SmartSell/1.0",
    }


def _epoch_ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def _safe_truncate(text: str, limit: int = 1000) -> str:
    if limit <= 0:
        return ""
    return text[:limit]


def _split_windows(initial_minutes: int, min_minutes: int) -> list[int]:
    initial = max(1, int(initial_minutes))
    min_value = max(1, int(min_minutes))
    windows = [initial]
    while windows[-1] > min_value:
        next_value = max(min_value, windows[-1] // 2)
        if next_value == windows[-1]:
            break
        windows.append(next_value)
        if next_value == min_value:
            break
    return windows


def _extract_total_count(resp: httpx.Response) -> int | None:
    try:
        payload = resp.json() or {}
    except Exception:
        return None
    meta = payload.get("meta") if isinstance(payload, dict) else None
    if isinstance(meta, dict) and "totalCount" in meta:
        try:
            return int(meta.get("totalCount"))
        except (TypeError, ValueError):
            return None
    return None


def _print_failure(resp: httpx.Response, elapsed_ms: int) -> None:
    content_type = resp.headers.get("content-type")
    text = ""
    try:
        text = resp.text or ""
    except Exception:
        text = ""
    snippet = _safe_truncate(text, 1000)
    print(
        "status_code={} elapsed_ms={} content_type={} body_snippet={}".format(
            resp.status_code,
            elapsed_ms,
            content_type,
            snippet,
        )
    )


async def _run(args: argparse.Namespace) -> int:
    token = args.token.strip()
    now = datetime.now(timezone.utc)

    headers = _build_headers(token)

    client_kwargs: dict[str, object] = {
        "timeout": args.timeout,
        "trust_env": False,
    }
    if args.http2:
        client_kwargs["http2"] = True
    else:
        client_kwargs["transport"] = httpx.AsyncHTTPTransport(http2=False)

    windows = [args.window_minutes]
    if args.auto_split:
        windows = _split_windows(args.window_minutes, args.min_window_minutes)

    last_params: list[tuple[str, object]] | None = None

    async with httpx.AsyncClient(**client_kwargs) as client:
        for window_minutes in windows:
            date_from = now - timedelta(minutes=window_minutes)
            params = [
                ("page[number]", args.page_number),
                ("page[size]", args.page_size),
                ("filter[orders][creationDate][$ge]", _epoch_ms(date_from)),
                ("filter[orders][creationDate][$le]", _epoch_ms(now)),
            ]
            if args.merchant_uid:
                params.append(("filter[orders][merchantUid]", args.merchant_uid))
            if args.include_entries:
                params.append(("include[orders]", "entries"))

            last_params = params
            start = time.perf_counter()
            resp = await client.get("https://kaspi.kz/shop/api/v2/orders", headers=headers, params=params)
            elapsed_ms = int((time.perf_counter() - start) * 1000)
            total = _extract_total_count(resp)

            if args.auto_split:
                print(
                    "window_minutes={} status_code={} elapsed_ms={} totalCount={}".format(
                        window_minutes,
                        resp.status_code,
                        elapsed_ms,
                        total,
                    )
                )

            if resp.status_code == 200:
                print(
                    "elapsed_ms={} status_code={} totalCount={}".format(
                        elapsed_ms,
                        resp.status_code,
                        total,
                    )
                )
                if args.auto_split and last_params is not None:
                    print("final_params={}".format(last_params))
                return 0

            if args.auto_split and resp.status_code in {400, 422} and window_minutes > args.min_window_minutes:
                continue

            _print_failure(resp, elapsed_ms)
            return 1

    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Kaspi HTTPX probe for /shop/api/v2/orders")
    parser.add_argument("--token", default="", help="Kaspi X-Auth-Token")
    parser.add_argument("--merchant-uid", default="", help="Merchant UID")
    parser.add_argument("--page-number", type=int, default=1)
    parser.add_argument("--page-size", type=int, default=50)
    parser.add_argument("--window-minutes", type=int, default=60)
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--http2", action="store_true", help="Enable HTTP/2")
    parser.add_argument("--include-entries", action="store_true")
    parser.add_argument("--auto-split", action="store_true", help="Split window on 400/422 responses")
    parser.add_argument("--min-window-minutes", type=int, default=60)
    args = parser.parse_args()

    if not args.token:
        raise SystemExit("Missing --token")

    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
