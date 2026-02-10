import httpx

from scripts.kaspi_httpx_probe import _extract_total_count, _safe_truncate, _split_windows


def test_split_windows_halves_to_min():
    assert _split_windows(480, 60) == [480, 240, 120, 60]


def test_split_windows_stops_at_min():
    assert _split_windows(75, 60) == [75, 60]


def test_safe_truncate_respects_limit():
    assert _safe_truncate("abc", 2) == "ab"
    assert _safe_truncate("abc", 0) == ""


def test_extract_total_count_from_meta():
    resp = httpx.Response(200, json={"meta": {"totalCount": 3}})
    assert _extract_total_count(resp) == 3


def test_extract_total_count_missing_meta():
    resp = httpx.Response(200, json={"data": []})
    assert _extract_total_count(resp) is None
