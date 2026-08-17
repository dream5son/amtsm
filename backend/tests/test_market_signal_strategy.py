"""Unit tests for MarketSignalStrategy and PeakValleyStrategy."""

import pytest

from app.market_signal.feed import (
    BarWindowFeed,
    BaselineWindow,
    StaticBaselineFeed,
    VolumeWindow,
    compute_volume_window,
)
from app.market_signal.peak_valley import PeakValleyStrategy
from app.market_signal.peak_value_with_volume import PeakValueWithVolumeStrategy
from app.market_signal.strategy import (
    STRATEGY_PEAK_VALLEY,
    STRATEGY_PEAK_VALUE_WITH_VOLUME,
    MarketSignalStrategy,
    SignalReference,
    SignalRequest,
    StrategyParams,
    get_market_signal_strategy,
)


def _request(
    price: float,
    *,
    low_min: float = 0.0,
    high_max: float = 0.0,
    x: float = 1.0,
    y: float = 1.0,
    actual_n: int | None = None,
    requested_n: int | None = None,
) -> SignalRequest:
    return SignalRequest(
        price=price,
        feed=StaticBaselineFeed(
            BaselineWindow(
                low_min=low_min,
                high_max=high_max,
                actual_n=actual_n,
                requested_n=requested_n,
            )
        ),
        params=StrategyParams(x=x, y=y),
    )


def test_factory_defaults_to_peak_value_with_volume() -> None:
    strategy = get_market_signal_strategy()
    assert isinstance(strategy, PeakValueWithVolumeStrategy)
    assert isinstance(strategy, MarketSignalStrategy)
    named = get_market_signal_strategy(STRATEGY_PEAK_VALUE_WITH_VOLUME)
    assert isinstance(named, PeakValueWithVolumeStrategy)
    legacy = get_market_signal_strategy(STRATEGY_PEAK_VALLEY)
    assert isinstance(legacy, PeakValleyStrategy)


def test_factory_unknown_name() -> None:
    with pytest.raises(ValueError, match="unknown market signal strategy"):
        get_market_signal_strategy("ma_cross")


def test_factory_forwards_require_full_n() -> None:
    live = get_market_signal_strategy()
    backtest = get_market_signal_strategy(require_full_n=True)
    assert not live.require_full_n
    assert backtest.require_full_n


def test_peak_valley_buy_boundary() -> None:
    strategy = PeakValleyStrategy()
    assert strategy.evaluate(_request(110.0, low_min=100.0, x=1.1)).buy
    assert strategy.evaluate(_request(109.9, low_min=100.0, x=1.1)).buy
    assert not strategy.evaluate(_request(110.1, low_min=100.0, x=1.1)).buy


def test_peak_valley_require_full_n() -> None:
    strategy = PeakValleyStrategy(require_full_n=True)
    full = _request(100.0, low_min=100.0, actual_n=60, requested_n=60)
    degraded = _request(100.0, low_min=100.0, actual_n=20, requested_n=60)
    assert strategy.evaluate(full).buy
    assert not strategy.evaluate(degraded).buy

    live = PeakValleyStrategy()
    assert live.evaluate(degraded).buy


def test_peak_valley_require_full_n_missing_window_raises() -> None:
    strategy = PeakValleyStrategy(require_full_n=True)
    with pytest.raises(ValueError, match="are required"):
        strategy.evaluate(_request(100.0, low_min=100.0))


def test_peak_valley_sell_and_addon() -> None:
    strategy = PeakValleyStrategy()
    assert strategy.evaluate(_request(117.0, high_max=130.0, y=0.9)).sell
    assert not strategy.evaluate(_request(116.9, high_max=130.0, y=0.9)).sell

    assert strategy.evaluate(_request(110.0, low_min=100.0, x=1.1)).addon
    assert not strategy.evaluate(_request(110.1, low_min=100.0, x=1.1)).addon


def test_peak_valley_addon_ignores_require_full_n() -> None:
    strategy = PeakValleyStrategy(require_full_n=True)
    decision = strategy.evaluate(
        _request(100.0, low_min=100.0, actual_n=20, requested_n=60)
    )
    assert not decision.buy
    assert decision.addon


def test_peak_valley_references() -> None:
    strategy = PeakValleyStrategy()
    decision = strategy.evaluate(
        _request(108.0, low_min=100.0, high_max=130.0, x=1.1, y=0.9)
    )
    assert decision.buy_reference == SignalReference(100.0, 1.1)
    assert decision.sell_reference == SignalReference(130.0, 0.9)


def _volume_request(
    price: float,
    *,
    volume: float | None,
    avg_volume: float = 1000.0,
    volume_n: int = 7,
    lookback: int = 7,
    low_min: float = 0.0,
    high_max: float = 0.0,
    x: float = 1.0,
    y: float = 1.0,
    actual_n: int | None = None,
    requested_n: int | None = None,
    buy_volume_increase_pct: float = 0.30,
    sell_volume_change_pct: float = 0.30,
) -> SignalRequest:
    return SignalRequest(
        price=price,
        volume=volume,
        feed=StaticBaselineFeed(
            BaselineWindow(
                low_min=low_min,
                high_max=high_max,
                actual_n=actual_n,
                requested_n=requested_n,
            ),
            VolumeWindow(
                avg_volume=avg_volume,
                actual_n=volume_n,
                lookback=lookback,
            ),
        ),
        params=StrategyParams(
            x=x,
            y=y,
            volume_lookback=lookback,
            buy_volume_increase_pct=buy_volume_increase_pct,
            sell_volume_change_pct=sell_volume_change_pct,
        ),
    )


def test_volume_buy_requires_surge() -> None:
    strategy = PeakValueWithVolumeStrategy()
    # Price at buy threshold (100 * 1.1 = 110) but volume only +10%.
    quiet = _volume_request(110.0, volume=1100.0, low_min=100.0, x=1.1)
    assert not strategy.evaluate(quiet).buy
    assert not strategy.evaluate(quiet).addon

    surged = _volume_request(110.0, volume=1300.0, low_min=100.0, x=1.1)
    decision = strategy.evaluate(surged)
    assert decision.buy
    assert decision.addon


def test_volume_sell_requires_absolute_change() -> None:
    strategy = PeakValueWithVolumeStrategy()
    # Price at sell threshold (130 * 0.9 = 117) but volume unchanged.
    flat = _volume_request(117.0, volume=1000.0, high_max=130.0, y=0.9)
    assert not strategy.evaluate(flat).sell

    dry_up = _volume_request(117.0, volume=700.0, high_max=130.0, y=0.9)
    assert strategy.evaluate(dry_up).sell

    surge = _volume_request(117.0, volume=1300.0, high_max=130.0, y=0.9)
    assert strategy.evaluate(surge).sell


def test_volume_missing_or_short_window_blocks() -> None:
    strategy = PeakValueWithVolumeStrategy()
    no_vol = _volume_request(110.0, volume=None, low_min=100.0, x=1.1)
    assert not strategy.evaluate(no_vol).buy

    zero_vol = _volume_request(110.0, volume=0.0, low_min=100.0, x=1.1)
    assert not strategy.evaluate(zero_vol).buy

    short = _volume_request(
        110.0, volume=2000.0, low_min=100.0, x=1.1, volume_n=6, lookback=7
    )
    assert not strategy.evaluate(short).buy
    assert not strategy.evaluate(short).sell


def test_volume_strategy_keeps_price_references() -> None:
    strategy = PeakValueWithVolumeStrategy()
    decision = strategy.evaluate(
        _volume_request(
            108.0,
            volume=1300.0,
            low_min=100.0,
            high_max=130.0,
            x=1.1,
            y=0.9,
        )
    )
    assert decision.buy_reference == SignalReference(100.0, 1.1)
    assert decision.sell_reference == SignalReference(130.0, 0.9)


def test_compute_volume_window_and_bar_feed() -> None:
    bars = [
        {"date": f"2024-01-{i:02d}", "open": 10, "high": 10, "low": 10, "close": 10, "volume": 1000}
        for i in range(1, 8)
    ]
    window = compute_volume_window(bars, 7)
    assert window.actual_n == 7
    assert window.avg_volume == pytest.approx(1000.0)

    short = compute_volume_window(bars[:3], 7)
    assert short.actual_n == 3
    assert short.avg_volume == pytest.approx(1000.0)

    feed = BarWindowFeed(bars, requested_n=3)
    assert feed.volume_window(7).actual_n == 7
    assert feed.baseline().actual_n == 3
