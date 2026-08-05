import pytest

from app.engine.baseline_calculator import InsufficientDataError, compute_baseline


def _make_bars(n: int) -> list[dict]:
    return [{"date": f"2024-01-{i+1:02d}", "open": 10.0, "high": 10.0 + i, "low": 9.0 - i * 0.1, "close": 10.0, "volume": 1000} for i in range(n)]


def test_compute_baseline_normal():
    bars = _make_bars(60)
    low_min, high_max, actual_n = compute_baseline(bars, 60)
    assert actual_n == 60
    assert low_min == min(b["low"] for b in bars)
    assert high_max == max(b["high"] for b in bars)


def test_compute_baseline_degraded():
    bars = _make_bars(10)
    low_min, high_max, actual_n = compute_baseline(bars, 60)
    assert actual_n == 10
    assert low_min == min(b["low"] for b in bars)
    assert high_max == max(b["high"] for b in bars)


def test_compute_baseline_no_data():
    with pytest.raises(InsufficientDataError):
        compute_baseline([], 60)
