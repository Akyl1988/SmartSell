from scripts.kaspi_httpx_probe import _split_windows


def test_split_windows_exact_halves():
    assert _split_windows(480, 60) == [480, 240, 120, 60]


def test_split_windows_stops_at_min():
    assert _split_windows(75, 60) == [75, 60]


def test_split_windows_below_min_single_attempt():
    assert _split_windows(30, 60) == [30]
