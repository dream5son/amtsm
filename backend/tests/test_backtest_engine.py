from __future__ import annotations

from app.schemas.strategy import (
    DEFAULT_BREAK_EVEN_BUFFER_PCT,
    DEFAULT_BREAK_EVEN_TRIGGER_PCT,
    DEFAULT_STOP_LOSS_PCT,
    DEFAULT_TRAILING_LADDER,
    LEGACY_TRAILING_LADDER,
    parse_trailing_ladder,
)
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
        trailing_ladder=parse_trailing_ladder(LEGACY_TRAILING_LADDER),
        enable_partial_take_profit=False,
    )
    base.update(overrides)
    return RiskParams(**base)


def _growth_risk() -> RiskParams:
    return RiskParams(
        stop_loss_pct=DEFAULT_STOP_LOSS_PCT,
        break_even_trigger_pct=DEFAULT_BREAK_EVEN_TRIGGER_PCT,
        break_even_buffer_pct=DEFAULT_BREAK_EVEN_BUFFER_PCT,
        trailing_ladder=parse_trailing_ladder(DEFAULT_TRAILING_LADDER),
        enable_partial_take_profit=False,
    )


_QUIET_VOL = 1000.0
_SURGE_VOL = 2000.0


def _bar(
    date: str,
    open_: float,
    high: float,
    low: float,
    close: float,
    volume: float = _QUIET_VOL,
) -> dict:
    return {
        "date": date,
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
    }


# Seven quiet days so volume lookback is complete; last three keep low_min=9.4
# for n=3 (buy threshold = 9.4 * 1.10 = 10.34).
_WARMUP = [
    _bar("2023-12-26", 10.0, 10.2, 9.9, 10.0),
    _bar("2023-12-27", 10.0, 10.2, 9.9, 10.0),
    _bar("2023-12-28", 10.0, 10.2, 9.9, 10.0),
    _bar("2023-12-29", 10.0, 10.2, 9.9, 10.0),
    _bar("2024-01-02", 9.5, 9.7, 9.5, 9.6),
    _bar("2024-01-03", 9.6, 9.8, 9.6, 9.7),
    _bar("2024-01-04", 9.4, 9.6, 9.4, 9.5),
]
_START_DATE = "2024-01-05"


def test_buy_then_stop_loss_exit() -> None:
    bars = _WARMUP + [
        _bar(_START_DATE, 10.0, 10.1, 9.9, 10.0, _SURGE_VOL),  # entry: close 10.0 <= 10.34
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
        _bar(_START_DATE, 10.0, 10.1, 9.9, 10.0, _SURGE_VOL),  # entry at 10.0
        # +15% high; profit-based stop = 10 + 1.5*0.85 = 11.275; low 11.0 fills.
        _bar("2024-01-08", 11.0, 11.5, 11.0, 11.3),
    ]
    risk = _params()
    result = run_backtest(bars, start_date=_START_DATE, n=3, x=1.10, risk=risk)

    assert len(result.trades) == 1
    trade = result.trades[0]
    assert trade.entry_price == 10.0
    assert trade.exit_date == "2024-01-08"
    assert trade.exit_reason == EXIT_TAKE_PROFIT
    assert abs(trade.exit_price - 11.0) < 1e-9
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
    assert abs(step2.stop_price - 11.275) < 1e-9
    assert 11.0 <= step2.stop_price  # day-2 low breaches -> same-day exit
    manual_exit_price = min(11.0, step2.stop_price)
    assert abs(manual_exit_price - trade.exit_price) < 1e-9
    manual_reason = EXIT_STOP_LOSS if manual_exit_price < 10.0 - 1e-12 else EXIT_TAKE_PROFIT
    assert manual_reason == trade.exit_reason


def test_period_end_when_still_holding_at_range_end() -> None:
    bars = _WARMUP + [
        _bar(_START_DATE, 10.0, 10.1, 9.9, 10.0, _SURGE_VOL),
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
        _bar(_START_DATE, 10.0, 10.1, 9.9, 10.0, _SURGE_VOL),
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
        _bar(_START_DATE, 10.0, 10.1, 9.9, 10.0, _SURGE_VOL),
        _bar("2024-01-08", 11.0, 11.5, 11.0, 11.3),
    ]
    risk = _params(enable_partial_take_profit=True)
    result = run_backtest(bars, start_date=_START_DATE, n=3, x=1.10, risk=risk)
    # Same trade as the take-profit test above — partial TP flag has no effect.
    assert len(result.trades) == 1
    assert result.trades[0].exit_reason == EXIT_TAKE_PROFIT


def test_halted_day_with_zero_ohlc_voids_without_stop_loss() -> None:
    """A non-positive OHLC day is incomparable: void the open position instead
    of deleting the row (which would punch a hole) or recording STOP_LOSS."""
    bars = _WARMUP + [
        _bar(_START_DATE, 10.0, 10.1, 9.9, 10.0, _SURGE_VOL),  # entry
        _bar("2024-01-07", 0.0, 0.0, 0.0, 0.0),  # incomparable while holding
        _bar("2024-01-08", 20.0, 20.5, 19.9, 20.2),  # far above buy threshold
    ]
    result = run_backtest(bars, start_date=_START_DATE, n=3, x=1.10, risk=_params())
    assert result.trades == []
    assert result.summary.trade_count == 0


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
    bars = [
        _bar("2024-01-02", 10.0, 10.2, 9.9, 10.0),  # idx=0 skipped
        _bar("2024-01-03", 9.5, 9.6, 9.4, 9.5),  # idx=1 skipped; would buy on a 1-day window
        _bar("2024-01-04", 9.5, 9.6, 9.4, 9.5),  # idx=2 skipped
        _bar("2024-01-05", 9.5, 9.6, 9.4, 9.5),  # idx=3: first full n=3, low_min=9.4, thr=10.34
        _bar("2024-01-08", 9.5, 9.6, 9.4, 9.5),
    ]
    result = run_backtest(bars, start_date="2024-01-02", n=3, x=1.10, risk=_params())
    assert len(result.trades) == 1
    assert result.trades[0].entry_date == "2024-01-05"
    assert result.trades[0].entry_price == 9.5
    assert result.trades[0].exit_reason == EXIT_PERIOD_END


def test_dirty_low_does_not_poison_baseline_buy() -> None:
    """A micro-low bar is incomparable so it must not enter the N-day window."""
    warmup = [
        _bar("2023-12-26", 10.0, 10.2, 9.9, 10.0),
        _bar("2023-12-27", 10.0, 10.2, 9.9, 10.0),
        _bar("2023-12-28", 10.0, 10.2, 9.9, 10.0),
        _bar("2023-12-29", 10.0, 10.2, 9.9, 10.0),
        _bar("2024-01-02", 10.0, 10.2, 9.9, 10.0),
        _bar("2024-01-03", 10.1, 10.3, 10.0, 10.1),
        _bar("2024-01-04", 0.5, 1.0, 0.02, 0.3),  # dirty — masked, not deleted
        _bar("2024-01-05", 10.0, 10.2, 9.8, 10.0),  # reconnects; becomes warm-up
    ]
    # After sanitize: seven clean bars ending 10.0; start on 01-08 needs n=3
    # prior -> window low_min=9.8, thr=10.78; close 10.5 buys at close.
    bars = warmup + [
        _bar("2024-01-08", 10.4, 10.6, 10.3, 10.5, _SURGE_VOL),
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
        _bar(_START_DATE, 10.0, 10.1, 9.9, 10.0, _SURGE_VOL),
    ]
    result = run_backtest(bars, start_date=_START_DATE, n=3, x=1.10, risk=_params())
    assert len(result.trades) == 1
    trade = result.trades[0]
    low_min = 9.4
    threshold = low_min * 1.10
    assert trade.entry_price == 10.0
    assert trade.entry_price <= threshold
    assert trade.entry_price != threshold


def test_quiet_volume_still_enters() -> None:
    """Buy-volume gate is off by default so a quiet pullback can still enter."""
    bars = _WARMUP + [
        _bar(_START_DATE, 10.0, 10.1, 9.9, 10.0),  # price buys, volume quiet
    ]
    result = run_backtest(bars, start_date=_START_DATE, n=3, x=1.10, risk=_params())
    assert len(result.trades) == 1
    assert result.trades[0].entry_date == _START_DATE
    assert result.trades[0].exit_reason == EXIT_PERIOD_END


def test_entry_when_volume_key_missing() -> None:
    """With the buy-volume gate off, missing volume no longer blocks entry."""
    source = _WARMUP + [_bar(_START_DATE, 10.0, 10.1, 9.9, 10.0, _SURGE_VOL)]
    bars = [{k: v for k, v in bar.items() if k != "volume"} for bar in source]
    result = run_backtest(bars, start_date=_START_DATE, n=3, x=1.10, risk=_params())
    assert len(result.trades) == 1
    assert result.trades[0].entry_date == _START_DATE
    assert result.trades[0].exit_reason == EXIT_PERIOD_END


def test_hold_across_large_calendar_gap_voids_without_exit_trade() -> None:
    """Mirrors 长江电力: buy, then sanitize leaves a multi-year hole; the next
    valid bar would ratchet to break-even and print TAKE_PROFIT. Continuity
    gate must void the position instead of emitting an orphan sell."""
    bars = _WARMUP + [
        _bar(_START_DATE, 10.0, 10.1, 9.9, 10.0, _SURGE_VOL),  # entry
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
        _bar(_START_DATE, 10.0, 10.1, 9.9, 10.0, _SURGE_VOL),  # entry Fri 2024-01-05
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


def test_negative_qfq_prefix_does_not_shrink_series() -> None:
    """Leading non-positive qfq bars stay in the list; replay starts later."""
    prefix = [
        _bar("2023-12-20", -1.0, -0.9, -1.1, -1.0),
        _bar("2023-12-21", -0.8, -0.7, -0.9, -0.75),
    ]
    bars = prefix + _WARMUP + [
        _bar(_START_DATE, 10.0, 10.1, 9.9, 10.0, _SURGE_VOL),
    ]
    result = run_backtest(bars, start_date=_START_DATE, n=3, x=1.10, risk=_params())
    assert result.summary.qfq_prefix_skipped == 2
    assert result.summary.effective_start_date == _START_DATE
    assert len(result.trades) == 1
    assert result.trades[0].entry_date == _START_DATE
    assert result.trades[0].exit_reason == EXIT_PERIOD_END


def test_mid_series_negative_qfq_voids_without_stop_loss() -> None:
    bars = _WARMUP + [
        _bar(_START_DATE, 10.0, 10.1, 9.9, 10.0, _SURGE_VOL),
        _bar("2024-01-08", -14.71, -14.66, -14.71, -14.66),
        _bar("2024-01-09", 20.0, 20.5, 19.9, 20.2),
    ]
    result = run_backtest(bars, start_date=_START_DATE, n=3, x=1.10, risk=_params())
    assert result.trades == []
    assert result.summary.trade_count == 0
    assert result.summary.qfq_prefix_skipped == 0


def test_unadjusted_bonus_share_gap_triggers_stop_loss() -> None:
    """长江电力 2010-07-20 10转5: unadjusted open ≈ (prev - dividend) / 1.5."""
    bars = _WARMUP + [
        _bar(_START_DATE, 12.12, 12.3, 12.0, 12.12, _SURGE_VOL),
        _bar("2024-01-08", 8.11, 8.14, 8.02, 8.13),
    ]
    result = run_backtest(bars, start_date=_START_DATE, n=3, x=2.0, risk=_params())
    assert len(result.trades) == 1
    assert result.trades[0].exit_reason == EXIT_STOP_LOSS
    assert result.trades[0].pnl_pct < -0.15


def test_qfq_ex_rights_day_does_not_false_stop() -> None:
    """On a qfq series the same corporate action is a small continuous move."""
    bars = _WARMUP + [
        _bar(_START_DATE, 8.10, 8.20, 8.05, 8.12, _SURGE_VOL),
        _bar("2024-01-08", 8.11, 8.18, 8.08, 8.15),
    ]
    result = run_backtest(bars, start_date=_START_DATE, n=3, x=1.10, risk=_params())
    assert len(result.trades) == 1
    assert result.trades[0].exit_reason == EXIT_PERIOD_END
    assert result.trades[0].pnl_pct > 0


def test_voided_gap_allows_fresh_buy_after_window_rebuilds() -> None:
    """After a hold is voided by a large gap, a later continuous N-day window
    may enter again and complete a normal stop-loss trade."""
    bars = _WARMUP + [
        _bar(_START_DATE, 10.0, 10.1, 9.9, 10.0, _SURGE_VOL),  # first entry — later voided
        # Large hole voids the open position; no SL/TP row.
        _bar("2024-03-01", 10.0, 10.2, 9.9, 10.0),
        # Rebuild a continuous N=3 baseline after the hole (Mar1/4/5 → buy on Mar6).
        _bar("2024-03-04", 10.0, 10.2, 9.4, 9.8),
        _bar("2024-03-05", 9.8, 9.9, 9.5, 9.7),
        # low_min of prior 3 = 9.4, thr=10.34; close 9.6 buys
        _bar("2024-03-06", 9.7, 9.8, 9.5, 9.6, _SURGE_VOL),
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


def test_detect_unadjusted_corporate_actions_flags_ex_rights_gap() -> None:
    """sh600900 2010-07-20 十转五 on an unadjusted series: 12.12 → 8.11."""
    from app.services.backtest_engine import detect_unadjusted_corporate_actions

    bars = [
        _bar("2024-01-04", 12.0, 12.3, 11.9, 12.12),
        _bar("2024-01-05", 8.11, 8.14, 8.02, 8.13),
    ]
    assert detect_unadjusted_corporate_actions(bars) == ["2024-01-05"]


def test_validate_bar_series_accepts_qfq_ex_rights_sequence() -> None:
    from app.services.backtest_engine import validate_bar_series

    bars = [
        _bar("2024-01-04", 8.10, 8.20, 8.05, 8.12),
        _bar("2024-01-05", 8.11, 8.18, 8.08, 8.15),
    ]
    validate_bar_series(bars)


def test_validate_bar_series_rejects_unadjusted_sequence() -> None:
    import pytest

    from app.services.backtest_engine import validate_bar_series

    bars = [
        _bar("2024-01-04", 12.0, 12.3, 11.9, 12.12),
        _bar("2024-01-05", 8.11, 8.14, 8.02, 8.13),
    ]
    with pytest.raises(ValueError, match="unadjusted"):
        validate_bar_series(bars)


def test_sh600900_qfq_ex_rights_not_stop_loss() -> None:
    """Regression: qfq 除权日不应产生 -33% 假止损（见 job #18 不复权 bug）。"""
    bars = _WARMUP + [
        _bar(_START_DATE, 8.10, 8.20, 8.05, 8.12, _SURGE_VOL),
        _bar("2024-01-08", 8.11, 8.18, 8.08, 8.15),
    ]
    result = run_backtest(bars, start_date=_START_DATE, n=3, x=1.10, risk=_params())
    assert len(result.trades) == 1
    trade = result.trades[0]
    assert trade.exit_reason == EXIT_PERIOD_END
    assert trade.pnl_pct > 0
    assert trade.pnl_pct < 0.05


# Real sh600900 qfq around the 2010-07-20 十转五 (from daily_market_snapshots).
_SH600900_QFQ_EX_RIGHTS_WEEK = [
    _bar("2010-07-15", 4.4734, 4.5131, 4.4409, 4.4481),
    _bar("2010-07-16", 4.4445, 4.4950, 4.4048, 4.4734),
    _bar("2010-07-19", 4.4770, 4.5275, 4.4553, 4.5239),
    _bar("2010-07-20", 4.5239, 4.5407, 4.4737, 4.5351),
    _bar("2010-07-21", 4.5351, 4.6578, 4.4626, 4.5574),
    _bar("2010-07-22", 4.5295, 4.5685, 4.5072, 4.5462),
    _bar("2010-07-23", 4.5630, 4.5797, 4.5128, 4.5239),
]


def test_sh600900_qfq_ex_rights_week_has_no_unadjusted_gap() -> None:
    from app.services.backtest_engine import detect_unadjusted_corporate_actions

    assert detect_unadjusted_corporate_actions(_SH600900_QFQ_EX_RIGHTS_WEEK) == []


def test_sh600900_unadjusted_job18_gap_is_flagged() -> None:
    """Job #18 unadjusted 12.12 → 8.11 must keep tripping the safety net."""
    from app.services.backtest_engine import detect_unadjusted_corporate_actions

    bars = [
        _bar("2010-07-19", 12.0, 12.3, 11.9, 12.12),
        _bar("2010-07-20", 8.11, 8.14, 8.02, 8.13),
    ]
    assert detect_unadjusted_corporate_actions(bars) == ["2010-07-20"]


def test_detect_skips_share_reform_resume_after_zero_volume_halt() -> None:
    """sz000651 2006-03-08 股改复牌: halt fills then ~24% gap is still qfq."""
    from app.services.backtest_engine import detect_unadjusted_corporate_actions

    bars = [
        _bar("2006-02-20", 0.55, 0.572, 0.5412, 0.56364, 4_099_356.0),
        _bar("2006-03-07", 0.56364, 0.56364, 0.56364, 0.56364, 0.0),
        _bar("2006-03-08", 0.42592, 0.4378, 0.4202, 0.42108, 4_363_122.0),
    ]
    assert detect_unadjusted_corporate_actions(bars) == []


def test_detect_skips_share_reform_resume_when_halt_dates_omitted() -> None:
    """Same 股改 gap when the provider drops halt days instead of filling them."""
    from app.services.backtest_engine import detect_unadjusted_corporate_actions

    bars = [
        _bar("2006-02-20", 0.55, 0.572, 0.5412, 0.56364, 4_099_356.0),
        _bar("2006-03-08", 0.42592, 0.4378, 0.4202, 0.42108, 4_363_122.0),
    ]
    assert detect_unadjusted_corporate_actions(bars) == []


def test_unadjusted_ex_rights_after_weekend_is_still_flagged() -> None:
    """Friday→Monday 10转5 must still fail the safety net (not a halt)."""
    from app.services.backtest_engine import detect_unadjusted_corporate_actions

    bars = [
        _bar("2010-07-16", 12.0, 12.3, 11.9, 12.12),
        _bar("2010-07-19", 8.11, 8.14, 8.02, 8.13),
    ]
    assert detect_unadjusted_corporate_actions(bars) == ["2010-07-19"]


def test_trailing_band_locks_gain_instead_of_stop_below_cost() -> None:
    """+22% wick locks ~80% of the gain; never a -2% stop (sh600900 2011-07-26).

    stop2 = 3.90 + (4.76-3.90)*0.80 = 4.588; same-day low 4.29 fills at open 4.34.
    """
    warmup = [
        _bar("2023-12-26", 4.10, 4.15, 4.05, 4.10),
        _bar("2023-12-27", 4.10, 4.15, 4.05, 4.10),
        _bar("2023-12-28", 4.10, 4.15, 4.05, 4.10),
        _bar("2023-12-29", 4.10, 4.15, 4.05, 4.10),
        _bar("2024-01-02", 4.00, 4.05, 3.95, 4.00),
        _bar("2024-01-03", 4.00, 4.05, 3.95, 3.98),
        _bar("2024-01-04", 3.95, 4.00, 3.90, 3.95),
    ]
    bars = warmup + [
        _bar("2024-01-05", 3.94, 3.95, 3.89, 3.90, _SURGE_VOL),
        _bar("2024-01-08", 4.34, 4.76, 4.29, 4.29),
    ]
    result = run_backtest(bars, start_date="2024-01-05", n=3, x=1.10, risk=_growth_risk())
    assert len(result.trades) == 1
    trade = result.trades[0]
    assert trade.entry_date == "2024-01-05"
    assert abs(trade.entry_price - 3.90) < 1e-9
    assert trade.exit_reason == EXIT_TAKE_PROFIT
    assert trade.exit_date == "2024-01-08"
    assert abs(trade.exit_price - 4.34) < 1e-9
    assert trade.pnl_pct > 0.10


def test_sh600660_style_trailing_locks_most_of_peak_gain() -> None:
    """Peak +37% must lock ~+30%, not give back to +9.7% (sh600660 2007-11-29).

    stop2 = 7.34 + (10.07-7.34)*0.80 = 9.524. Peak-day low stays above that;
    the next day's 9.40 low fills at the stop.
    """
    warmup = [
        _bar("2023-12-26", 7.50, 7.55, 7.45, 7.50),
        _bar("2023-12-27", 7.50, 7.55, 7.45, 7.50),
        _bar("2023-12-28", 7.50, 7.55, 7.45, 7.50),
        _bar("2023-12-29", 7.50, 7.55, 7.45, 7.50),
        _bar("2024-01-02", 7.40, 7.45, 7.35, 7.40),
        _bar("2024-01-03", 7.40, 7.45, 7.35, 7.38),
        _bar("2024-01-04", 7.36, 7.40, 7.34, 7.36),
    ]
    bars = warmup + [
        _bar("2024-01-05", 7.35, 7.36, 7.30, 7.34, _SURGE_VOL),
        _bar("2024-01-08", 9.90, 10.07, 9.60, 9.70),
        _bar("2024-01-09", 9.60, 9.60, 9.40, 9.45),
    ]
    result = run_backtest(bars, start_date="2024-01-05", n=3, x=1.10, risk=_growth_risk())
    assert len(result.trades) == 1
    trade = result.trades[0]
    assert trade.entry_date == "2024-01-05"
    assert abs(trade.entry_price - 7.34) < 1e-9
    assert trade.exit_reason == EXIT_TAKE_PROFIT
    expected_stop = 7.34 + (10.07 - 7.34) * 0.80
    assert abs(trade.exit_price - expected_stop) < 1e-9
    assert trade.pnl_pct > 0.25
    assert abs(trade.pnl_pct - 0.097) > 0.05


def test_replay_sh600900_from_local_snapshots_no_ex_rights_stop() -> None:
    """Replay cached qfq for 长江电力; 2010-07-20 must not be a -33% false stop."""
    import sqlite3
    from pathlib import Path

    import pytest

    from app.services.backtest_engine import detect_unadjusted_corporate_actions

    db_path = Path(__file__).resolve().parents[2] / "data" / "amtsm.db"
    if not db_path.exists():
        pytest.skip("local data/amtsm.db is not present")
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT trade_date, open_price, high_price, low_price, close_price, volume "
            "FROM daily_market_snapshots WHERE stock_code = 'sh600900' "
            "ORDER BY trade_date"
        ).fetchall()
    finally:
        conn.close()
    if len(rows) < 100:
        pytest.skip("sh600900 snapshots are missing from local db")

    bars = [
        {
            "date": trade_date,
            "open": open_price,
            "high": high_price,
            "low": low_price,
            "close": close_price,
            "volume": volume,
        }
        for trade_date, open_price, high_price, low_price, close_price, volume in rows
    ]
    assert detect_unadjusted_corporate_actions(bars) == []

    result = run_backtest(
        bars,
        start_date="2000-01-01",
        n=60,
        x=1.10,
        risk=_growth_risk(),
    )
    bad = [
        trade
        for trade in result.trades
        if trade.exit_date == "2010-07-20"
        and trade.exit_reason == EXIT_STOP_LOSS
        and trade.pnl_pct < -0.20
    ]
    assert not bad

    false_trailing = [
        trade
        for trade in result.trades
        if trade.entry_date == "2011-07-26"
        and trade.exit_reason == EXIT_STOP_LOSS
        and abs(trade.pnl_pct + 0.023) < 0.01
    ]
    assert not false_trailing


def test_replay_sh600660_from_local_snapshots_no_premature_tp() -> None:
    """Replay cached qfq for 福耀玻璃; 2007-11-29 must not take profit at ~+9.7%."""
    import sqlite3
    from pathlib import Path

    import pytest

    from app.services.backtest_engine import detect_unadjusted_corporate_actions

    db_path = Path(__file__).resolve().parents[2] / "data" / "amtsm.db"
    if not db_path.exists():
        pytest.skip("local data/amtsm.db is not present")
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT trade_date, open_price, high_price, low_price, close_price, volume "
            "FROM daily_market_snapshots WHERE stock_code = 'sh600660' "
            "ORDER BY trade_date"
        ).fetchall()
    finally:
        conn.close()
    if len(rows) < 100:
        pytest.skip("sh600660 snapshots are missing from local db")

    bars = [
        {
            "date": trade_date,
            "open": open_price,
            "high": high_price,
            "low": low_price,
            "close": close_price,
            "volume": volume,
        }
        for trade_date, open_price, high_price, low_price, close_price, volume in rows
    ]
    assert detect_unadjusted_corporate_actions(bars) == []

    result = run_backtest(
        bars,
        start_date="2000-01-01",
        n=60,
        x=1.10,
        risk=_growth_risk(),
    )
    premature = [
        trade
        for trade in result.trades
        if trade.entry_date == "2007-11-29"
        and trade.exit_reason == EXIT_TAKE_PROFIT
        and abs(trade.pnl_pct - 0.097) < 0.01
    ]
    assert not premature


def test_replay_sz000651_from_local_snapshots_accepts_share_reform_gap() -> None:
    """Replay cached qfq for 格力电器; 2006-03-08 股改复牌 must not fail validation."""
    import sqlite3
    from pathlib import Path

    import pytest

    from app.services.backtest_engine import (
        detect_unadjusted_corporate_actions,
        validate_bar_series,
    )

    db_path = Path(__file__).resolve().parents[2] / "data" / "amtsm.db"
    if not db_path.exists():
        pytest.skip("local data/amtsm.db is not present")
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT trade_date, open_price, high_price, low_price, close_price, volume "
            "FROM daily_market_snapshots WHERE stock_code = 'sz000651' "
            "ORDER BY trade_date"
        ).fetchall()
    finally:
        conn.close()
    if len(rows) < 100:
        pytest.skip("sz000651 snapshots are missing from local db")

    bars = [
        {
            "date": trade_date,
            "open": open_price,
            "high": high_price,
            "low": low_price,
            "close": close_price,
            "volume": volume,
        }
        for trade_date, open_price, high_price, low_price, close_price, volume in rows
    ]
    assert detect_unadjusted_corporate_actions(bars) == []
    validate_bar_series(bars)
