from __future__ import annotations

from app.schemas.strategy import parse_trailing_ladder
from app.services.backtest_engine import (
    EXIT_PERIOD_END,
    EXIT_STOP_LOSS,
    EXIT_TAKE_PROFIT,
    has_valid_ohlc,
    run_backtest,
    sanitize_bars_for_backtest,
)
from app.services.risk_rules import RiskParams, evaluate


def _params(**overrides) -> RiskParams:
    base = dict(
        stop_loss_pct=0.08,
        break_even_trigger_pct=0.10,
        break_even_buffer_pct=0.005,
        trailing_ladder=parse_trailing_ladder(None),
        enable_partial_take_profit=False,
    )
    base.update(overrides)
    return RiskParams(**base)


def _bar(date: str, open_: float, high: float, low: float, close: float) -> dict:
    return {"date": date, "open": open_, "high": high, "low": low, "close": close}


# Three warm-up days with low_min=9.4 -> buy threshold = 9.4 * 1.10 = 10.34.
_WARMUP = [
    _bar("2024-01-02", 9.5, 9.7, 9.5, 9.6),
    _bar("2024-01-03", 9.6, 9.8, 9.6, 9.7),
    _bar("2024-01-04", 9.4, 9.6, 9.4, 9.5),
]
_START_DATE = "2024-01-05"


def test_buy_then_stop_loss_exit() -> None:
    bars = _WARMUP + [
        _bar(_START_DATE, 10.0, 10.1, 9.9, 10.0),  # entry: close 10.0 <= 10.34
        _bar("2024-01-08", 9.0, 10.1, 8.9, 8.95),  # gap-down through stop
    ]
    result = run_backtest(bars, start_date=_START_DATE, n=3, x=1.10, risk=_params())

    assert len(result.trades) == 1
    trade = result.trades[0]
    assert trade.entry_date == _START_DATE
    assert trade.entry_price == 10.0
    assert trade.exit_date == "2024-01-08"
    assert trade.exit_reason == EXIT_STOP_LOSS
    # exit_price = min(open=9.0, stop=9.2) = 9.0
    assert abs(trade.exit_price - 9.0) < 1e-9
    assert abs(trade.pnl_pct - (-0.10)) < 1e-9
    assert abs(trade.pnl_amount - (9.0 - 10.0) * 100) < 1e-9

    assert result.summary.trade_count == 1
    assert result.summary.win_rate == 0.0
    assert result.summary.sample_insufficient is True


def test_buy_then_trailing_take_profit_exit() -> None:
    bars = _WARMUP + [
        _bar(_START_DATE, 10.0, 10.1, 9.9, 10.0),  # entry at 10.0
        _bar("2024-01-08", 11.0, 11.5, 11.0, 11.3),  # rally: ratchets stop to 10.05
        _bar("2024-01-09", 10.2, 11.6, 10.0, 10.1),  # pullback triggers exit
    ]
    risk = _params()
    result = run_backtest(bars, start_date=_START_DATE, n=3, x=1.10, risk=risk)

    assert len(result.trades) == 1
    trade = result.trades[0]
    assert trade.entry_price == 10.0
    assert trade.exit_date == "2024-01-09"
    assert trade.exit_reason == EXIT_TAKE_PROFIT
    assert abs(trade.exit_price - 10.05) < 1e-9
    assert trade.pnl_pct > 0

    # Cross-check against a hand-rolled risk_rules.evaluate replay (design.md
    # 关键点2: live engine and backtest must reuse the same rule core).
    step1 = evaluate(avg_cost=10.0, highest_since_hold=10.0, prev_stop=None, price=10.0, params=risk)
    assert abs(step1.stop_price - 9.2) < 1e-9

    step2 = evaluate(
        avg_cost=10.0,
        highest_since_hold=step1.highest_since_hold,
        prev_stop=step1.stop_price,
        price=11.5,
        params=risk,
    )
    assert abs(step2.stop_price - 10.05) < 1e-9
    assert 11.0 > step2.stop_price  # day-2 low does not breach -> no exit yet

    step3 = evaluate(
        avg_cost=10.0,
        highest_since_hold=step2.highest_since_hold,
        prev_stop=step2.stop_price,
        price=11.6,
        params=risk,
    )
    assert abs(step3.stop_price - 10.05) < 1e-9
    assert 10.0 <= step3.stop_price  # day-3 low breaches -> exit
    manual_exit_price = min(10.2, step3.stop_price)
    assert abs(manual_exit_price - trade.exit_price) < 1e-9
    manual_reason = EXIT_STOP_LOSS if manual_exit_price < 10.0 - 1e-12 else EXIT_TAKE_PROFIT
    assert manual_reason == trade.exit_reason


def test_period_end_when_still_holding_at_range_end() -> None:
    bars = _WARMUP + [
        _bar(_START_DATE, 10.0, 10.1, 9.9, 10.0),
        _bar("2024-01-08", 9.9, 10.2, 9.8, 10.1),  # stays above stop; range ends here
    ]
    result = run_backtest(bars, start_date=_START_DATE, n=3, x=1.10, risk=_params())

    assert len(result.trades) == 1
    trade = result.trades[0]
    assert trade.exit_reason == EXIT_PERIOD_END
    assert trade.exit_date == "2024-01-08"
    assert abs(trade.exit_price - 10.1) < 1e-9

    # PERIOD_END must never leak into the summary stats.
    assert result.summary.trade_count == 0
    assert result.summary.win_rate is None
    assert result.summary.avg_win_loss_ratio is None
    assert result.summary.max_drawdown is None
    assert result.summary.sample_insufficient is True


def test_no_entry_when_price_above_threshold() -> None:
    bars = _WARMUP + [
        _bar(_START_DATE, 20.0, 20.5, 19.9, 20.2),  # far above 10.34 threshold
    ]
    result = run_backtest(bars, start_date=_START_DATE, n=3, x=1.10, risk=_params())
    assert result.trades == []
    assert result.summary.trade_count == 0


def test_sample_insufficient_respects_min_trade_count() -> None:
    bars = _WARMUP + [
        _bar(_START_DATE, 10.0, 10.1, 9.9, 10.0),
        _bar("2024-01-08", 9.0, 10.1, 8.9, 8.95),
    ]
    result = run_backtest(
        bars, start_date=_START_DATE, n=3, x=1.10, risk=_params(), min_trade_count=1
    )
    assert result.summary.trade_count == 1
    assert result.summary.sample_insufficient is False


def test_enable_partial_take_profit_is_forced_off() -> None:
    """Backtest never simulates PARTIAL_TP/ADDON, even if the resolved params enable it."""
    bars = _WARMUP + [
        _bar(_START_DATE, 10.0, 10.1, 9.9, 10.0),
        _bar("2024-01-08", 11.0, 11.5, 11.0, 11.3),
        _bar("2024-01-09", 10.2, 11.6, 10.0, 10.1),
    ]
    risk = _params(enable_partial_take_profit=True)
    result = run_backtest(bars, start_date=_START_DATE, n=3, x=1.10, risk=risk)
    # Same trade as the take-profit test above — partial TP flag has no effect.
    assert len(result.trades) == 1
    assert result.trades[0].exit_reason == EXIT_TAKE_PROFIT


def test_halted_day_with_zero_ohlc_is_dropped_not_traded() -> None:
    """A data-source glitch reporting all-zero OHLC on a halted day must be
    dropped like a missing bar, never fed into risk_rules.evaluate (which
    would otherwise raise ValueError on avg_cost/price <= 0)."""
    bars = _WARMUP + [
        _bar(_START_DATE, 10.0, 10.1, 9.9, 10.0),  # entry: close 10.0 <= 10.34
        _bar("2024-01-07", 0.0, 0.0, 0.0, 0.0),  # bogus halted-day bar while holding
        _bar("2024-01-08", 9.0, 10.1, 8.9, 8.95),  # gap-down through stop
    ]
    result = run_backtest(bars, start_date=_START_DATE, n=3, x=1.10, risk=_params())

    assert len(result.trades) == 1
    trade = result.trades[0]
    assert trade.entry_date == _START_DATE
    assert trade.entry_price == 10.0
    assert trade.exit_reason == EXIT_STOP_LOSS


def test_invalid_params_raise() -> None:
    import pytest

    with pytest.raises(ValueError):
        run_backtest(_WARMUP, start_date=_START_DATE, n=0, x=1.1, risk=_params())
    with pytest.raises(ValueError):
        run_backtest(_WARMUP, start_date=_START_DATE, n=3, x=0, risk=_params())


def test_has_valid_ohlc_rejects_extreme_range_and_structure() -> None:
    assert has_valid_ohlc(10.0, 10.5, 9.8, 10.2) is True
    # Near-zero low with large body — classic qfq poison for low_min.
    assert has_valid_ohlc(0.51, 1.01, 0.02, 0.31) is False
    # low above min(open, close)
    assert has_valid_ohlc(10.0, 10.5, 10.2, 10.1) is False
    # high below max(open, close)
    assert has_valid_ohlc(10.0, 10.05, 9.8, 10.2) is False


def test_sanitize_drops_dirty_low_keeps_large_but_valid_moves() -> None:
    bars = [
        _bar("2024-01-02", 10.0, 10.2, 9.9, 10.0),
        _bar("2024-01-03", 0.51, 1.01, 0.02, 0.31),  # extreme range -> drop
        _bar("2024-01-04", 10.1, 10.3, 10.0, 10.2),
        # Large day-to-day move must still be kept — a sequential gap filter
        # would cascade-delete the rest of a long qfq history.
        _bar("2024-01-05", 12.5, 12.8, 12.4, 12.6),
        _bar("2024-01-08", 10.0, 10.2, 9.9, 10.1),
    ]
    cleaned = sanitize_bars_for_backtest(bars)
    assert [b["date"] for b in cleaned] == [
        "2024-01-02",
        "2024-01-04",
        "2024-01-05",
        "2024-01-08",
    ]


def test_no_entry_until_full_n_day_window() -> None:
    """Partial windows must not trigger buys even if close looks cheap vs a short low_min."""
    # Days 0..n-1 are skipped (idx < n). First eligible day is idx=3.
    bars = [
        _bar("2024-01-02", 10.0, 10.2, 9.9, 10.0),
        _bar("2024-01-03", 9.5, 9.6, 9.4, 9.5),  # would be "buy" on 1-day window
        _bar("2024-01-04", 9.5, 9.6, 9.4, 9.5),
        # idx=3: first full N window; low_min=9.4, thr=10.34; close 9.5 buys.
        _bar("2024-01-05", 9.5, 9.6, 9.4, 9.5),
        _bar("2024-01-08", 9.5, 9.6, 9.4, 9.5),
    ]
    result = run_backtest(bars, start_date="2024-01-02", n=3, x=1.10, risk=_params())
    assert len(result.trades) == 1
    assert result.trades[0].entry_date == "2024-01-05"
    assert result.trades[0].entry_price == 9.5
    assert result.trades[0].exit_reason == EXIT_PERIOD_END


def test_dirty_low_does_not_poison_baseline_buy() -> None:
    """A micro-low bar must be sanitized out so a later real dip can still buy."""
    warmup = [
        _bar("2024-01-02", 10.0, 10.2, 9.9, 10.0),
        _bar("2024-01-03", 10.1, 10.3, 10.0, 10.1),
        _bar("2024-01-04", 0.5, 1.0, 0.02, 0.3),  # dirty — dropped
        _bar("2024-01-05", 10.0, 10.2, 9.8, 10.0),  # reconnects; becomes warm-up
    ]
    # After sanitize: three clean bars ending 10.0; start on 01-08 needs n=3
    # prior -> window low_min=9.8, thr=10.78; close 10.5 buys at close.
    bars = warmup + [
        _bar("2024-01-08", 10.4, 10.6, 10.3, 10.5),
    ]
    result = run_backtest(bars, start_date="2024-01-08", n=3, x=1.10, risk=_params())
    assert len(result.trades) == 1
    trade = result.trades[0]
    assert trade.entry_date == "2024-01-08"
    assert trade.entry_price == 10.5  # entry is close, not low_min * x
    low_min = 9.8
    assert trade.entry_price <= low_min * 1.10
    assert trade.exit_reason == EXIT_PERIOD_END


def test_entry_price_is_close_not_threshold() -> None:
    bars = _WARMUP + [
        _bar(_START_DATE, 10.0, 10.1, 9.9, 10.0),
    ]
    result = run_backtest(bars, start_date=_START_DATE, n=3, x=1.10, risk=_params())
    assert len(result.trades) == 1
    trade = result.trades[0]
    low_min = 9.4
    threshold = low_min * 1.10
    assert trade.entry_price == 10.0
    assert trade.entry_price <= threshold
    assert trade.entry_price != threshold


def test_hold_across_large_calendar_gap_voids_without_exit_trade() -> None:
    """Mirrors 长江电力: buy, then sanitize leaves a multi-year hole; the next
    valid bar would ratchet to break-even and print TAKE_PROFIT. Continuity
    gate must void the position instead of emitting an orphan sell."""
    bars = _WARMUP + [
        _bar(_START_DATE, 10.0, 10.1, 9.9, 10.0),  # entry
        # 40 calendar days later — would TP (high→break-even stop, low breaches)
        # without the gap void.
        _bar("2024-02-14", 11.0, 11.5, 10.0, 10.5),
    ]
    result = run_backtest(bars, start_date=_START_DATE, n=3, x=1.10, risk=_params())
    assert result.trades == []
    assert result.summary.trade_count == 0


def test_buy_skipped_when_baseline_window_has_large_gap() -> None:
    """N-day low_min is unusable when the window itself straddles a data hole."""
    bars = [
        _bar("2024-01-02", 10.0, 10.2, 9.9, 10.0),
        _bar("2024-01-03", 10.0, 10.2, 9.4, 9.5),  # sets a cheap low_min
        # 40-day hole inside what would become the N=3 window for 03-20
        _bar("2024-02-12", 10.0, 10.2, 9.9, 10.0),
        # close 9.5 <= 9.4*1.10 — would buy if the gap were ignored
        _bar("2024-02-13", 9.5, 9.6, 9.4, 9.5),
    ]
    result = run_backtest(bars, start_date="2024-01-02", n=3, x=1.10, risk=_params())
    assert result.trades == []


def test_continuous_hold_still_emits_stop_loss_across_short_holiday() -> None:
    """Gaps ≤30 calendar days (weekend / CNY-scale) must not void the position."""
    bars = _WARMUP + [
        _bar(_START_DATE, 10.0, 10.1, 9.9, 10.0),  # entry Fri 2024-01-05
        # 20 calendar days later — still continuous; gap-down through stop
        _bar("2024-01-25", 9.0, 10.1, 8.9, 8.95),
    ]
    result = run_backtest(bars, start_date=_START_DATE, n=3, x=1.10, risk=_params())

    assert len(result.trades) == 1
    trade = result.trades[0]
    assert trade.entry_date == _START_DATE
    assert trade.exit_date == "2024-01-25"
    assert trade.exit_reason == EXIT_STOP_LOSS
    assert abs(trade.exit_price - 9.0) < 1e-9


def test_voided_gap_allows_fresh_buy_after_window_rebuilds() -> None:
    """After a hold is voided by a large gap, a later continuous N-day window
    may enter again and complete a normal stop-loss trade."""
    bars = _WARMUP + [
        _bar(_START_DATE, 10.0, 10.1, 9.9, 10.0),  # first entry — later voided
        # Large hole voids the open position; no SL/TP row.
        _bar("2024-03-01", 10.0, 10.2, 9.9, 10.0),
        # Rebuild a continuous N=3 baseline after the hole (Mar1/4/5 → buy on Mar6).
        _bar("2024-03-04", 10.0, 10.2, 9.4, 9.8),
        _bar("2024-03-05", 9.8, 9.9, 9.5, 9.7),
        # low_min of prior 3 = 9.4, thr=10.34; close 9.6 buys
        _bar("2024-03-06", 9.7, 9.8, 9.5, 9.6),
        _bar("2024-03-07", 9.5, 9.6, 9.4, 9.5),  # still above stop
        _bar("2024-03-08", 8.5, 9.6, 8.4, 8.5),  # stop-loss exit
    ]
    result = run_backtest(bars, start_date=_START_DATE, n=3, x=1.10, risk=_params())

    assert len(result.trades) == 1
    trade = result.trades[0]
    assert trade.entry_date == "2024-03-06"
    assert trade.entry_price == 9.6
    assert trade.exit_date == "2024-03-08"
    assert trade.exit_reason == EXIT_STOP_LOSS
