"""Unit tests for entry (buy / tech-sell / addon) threshold rules."""

from app.market_signal.entry import (
    buy_threshold,
    is_addon_signal,
    is_buy_signal,
    is_tech_sell_signal,
    sell_threshold,
)


def test_buy_and_sell_thresholds() -> None:
    assert abs(buy_threshold(100.0, 1.1) - 110.0) < 1e-12
    assert abs(sell_threshold(130.0, 0.9) - 117.0) < 1e-12


def test_is_buy_signal_boundary() -> None:
    assert is_buy_signal(110.0, 100.0, 1.1)
    assert is_buy_signal(109.9, 100.0, 1.1)
    assert not is_buy_signal(110.1, 100.0, 1.1)


def test_is_buy_signal_require_full_n() -> None:
    # Full window: allowed.
    assert is_buy_signal(
        100.0, 100.0, 1.0, actual_n=60, n=60, require_full_n=True
    )
    # Degraded window: blocked when require_full_n.
    assert not is_buy_signal(
        100.0, 100.0, 1.0, actual_n=20, n=60, require_full_n=True
    )
    # Same degraded window: allowed for live (default).
    assert is_buy_signal(100.0, 100.0, 1.0, actual_n=20, n=60)


def test_is_tech_sell_and_addon() -> None:
    assert is_tech_sell_signal(117.0, 130.0, 0.9)
    assert not is_tech_sell_signal(116.9, 130.0, 0.9)
    assert is_addon_signal(110.0, 100.0, 1.1)
    assert not is_addon_signal(110.1, 100.0, 1.1)
