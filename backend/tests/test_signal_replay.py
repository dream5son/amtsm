"""Unit tests for daily-bar holding replay adapter."""

from app.schemas.strategy import LEGACY_TRAILING_LADDER, parse_trailing_ladder
from app.market_signal.replay import evaluate_bar_holding
from app.market_signal.risk import RiskParams
from app.market_signal.types import SIGNAL_STOP_LOSS, SIGNAL_TAKE_PROFIT


def _params(**overrides) -> RiskParams:
    base = dict(
        stop_loss_pct=0.08,
        break_even_trigger_pct=0.10,
        break_even_buffer_pct=0.005,
        trailing_ladder=parse_trailing_ladder(LEGACY_TRAILING_LADDER),
        enable_partial_take_profit=False,
    )
    base.update(overrides)
    return RiskParams(**base)


def test_holding_day_no_exit() -> None:
    result = evaluate_bar_holding(
        avg_cost=100.0,
        highest_since_hold=100.0,
        prev_stop=92.0,
        open_price=101.0,
        high=105.0,
        low=99.0,
        params=_params(),
    )
    assert result.exit_reason is None
    assert result.exit_price is None
    assert abs(result.highest_since_hold - 105.0) < 1e-12
    assert abs(result.stop_price - 92.0) < 1e-12


def test_holding_day_stop_loss_fill_at_open() -> None:
    # Gap open below stop → fill at open.
    result = evaluate_bar_holding(
        avg_cost=100.0,
        highest_since_hold=100.0,
        prev_stop=92.0,
        open_price=90.0,
        high=91.0,
        low=88.0,
        params=_params(),
    )
    assert result.exit_reason == SIGNAL_STOP_LOSS
    assert abs(result.exit_price - 90.0) < 1e-12


def test_holding_day_stop_loss_fill_at_stop() -> None:
    # Open above stop, low pierces stop → fill at stop.
    result = evaluate_bar_holding(
        avg_cost=100.0,
        highest_since_hold=100.0,
        prev_stop=92.0,
        open_price=95.0,
        high=96.0,
        low=90.0,
        params=_params(),
    )
    assert result.exit_reason == SIGNAL_STOP_LOSS
    assert abs(result.exit_price - 92.0) < 1e-12


def test_holding_day_take_profit_via_trailing() -> None:
    # High ratchets trailing stop above cost; low pierces it → TAKE_PROFIT.
    params = _params()
    # avg=100, high=121 → +21% (20-50% band, dd 10% of gain) → stop=118.9
    result = evaluate_bar_holding(
        avg_cost=100.0,
        highest_since_hold=120.0,
        prev_stop=110.4,
        open_price=112.0,
        high=121.0,
        low=109.0,
        params=params,
    )
    assert result.exit_reason == SIGNAL_TAKE_PROFIT
    assert result.exit_price is not None
    assert result.exit_price >= 100.0 - 1e-12
    assert abs(result.exit_price - 112.0) < 1e-12
