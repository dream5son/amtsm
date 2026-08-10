"""Pure day-by-day backtest replay engine.

Uses :mod:`app.market_signal` for buy thresholds, three-tier stops, and the OHLC
holding-day adapter so the live engine and backtest never drift (design.md
关键点2 "实盘与回测规则一致性"). This module owns sanitization, calendar-gap
voiding, PERIOD_END bookkeeping, and summary metrics.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from datetime import date as date_cls

from app.market_signal import (
    RiskParams,
    SIGNAL_PERIOD_END,
    SIGNAL_STOP_LOSS,
    SIGNAL_TAKE_PROFIT,
    compute_baseline,
    evaluate,
    evaluate_bar_holding,
    is_buy_signal,
)

logger = logging.getLogger(__name__)

EXIT_STOP_LOSS = SIGNAL_STOP_LOSS
EXIT_TAKE_PROFIT = SIGNAL_TAKE_PROFIT
EXIT_PERIOD_END = SIGNAL_PERIOD_END

# pnl_amount is quoted per virtual 1-lot (100 shares) — a simulation unit, not
# real money (design.md 5.5 backtest_trades.pnl_amount).
LOT_SIZE = 100

# Max (high-low)/close accepted for a single bar. Long qfq histories on
# high-dividend stocks often produce near-zero lows (e.g. low=0.02 with
# close≈2) that poison rolling low_min; recent clean A-share days stay well
# below this ceiling.
_MAX_INTRADAY_RANGE_PCT = 0.25

# Max calendar gap between consecutive bars still treated as continuous.
# Covers A-share long holidays (~11d) plus a few sanitized dirty bars; larger
# holes (e.g. multi-year qfq wipeouts) void open positions and block buys so
# STOP_LOSS/TAKE_PROFIT never fire across a data discontinuity.
_MAX_BAR_GAP_CALENDAR_DAYS = 30


def _max_consecutive_gap_days(dates: list[str]) -> int:
    """Largest calendar-day gap between any adjacent ISO dates (oldest-first)."""
    if len(dates) < 2:
        return 0
    return max(
        (date_cls.fromisoformat(b) - date_cls.fromisoformat(a)).days
        for a, b in zip(dates, dates[1:])
    )


def has_valid_ohlc(open_price: float, high_price: float, low_price: float, close_price: float) -> bool:
    """True when a bar looks like a tradable daily candle.

    Rejects:
    - zero/negative OHLC (halted-day glitches; early qfq going negative)
    - structurally impossible OHLC (low above body, high below body)
    - extreme intraday range that is almost always a forward-adjustment
      artifact rather than a real session (poisons N-day low_min)

    Shared by backtest replay and the kline display so the chart never shows
    a candle the simulation did not consume.
    """
    if not (open_price > 0 and high_price > 0 and low_price > 0 and close_price > 0):
        return False
    body_low = min(open_price, close_price)
    body_high = max(open_price, close_price)
    if low_price > body_low + 1e-12 or high_price < body_high - 1e-12:
        return False
    if (high_price - low_price) / close_price > _MAX_INTRADAY_RANGE_PCT + 1e-12:
        return False
    return True


def sanitize_bars_for_backtest(bars: list[dict]) -> list[dict]:
    """Drop bars that fail :func:`has_valid_ohlc`, preserving oldest-first order.

    Only per-bar checks are applied. A sequential close-gap filter was tried
    and rejected: dropping a dirty bar while keeping the previous close as the
    gap reference causes a cascade that wipes most of a long qfq history
    (e.g. 福耀玻璃 6522 → 69 bars), falsely triggering sample_insufficient.
    """
    return [
        bar
        for bar in bars
        if has_valid_ohlc(bar["open"], bar["high"], bar["low"], bar["close"])
    ]


@dataclass(frozen=True)
class BacktestTradeResult:
    entry_date: str
    entry_price: float
    exit_date: str
    exit_price: float
    hold_days: int
    pnl_pct: float
    pnl_amount: float
    exit_reason: str


@dataclass(frozen=True)
class BacktestSummary:
    win_rate: float | None
    avg_win_loss_ratio: float | None
    max_drawdown: float | None
    trade_count: int
    total_return: float | None
    annual_return: float | None
    sample_insufficient: bool


@dataclass(frozen=True)
class BacktestResult:
    trades: list[BacktestTradeResult]
    summary: BacktestSummary


def _make_trade(
    entry_date: str,
    entry_price: float,
    exit_date: str,
    exit_price: float,
    exit_reason: str,
) -> BacktestTradeResult:
    hold_days = (date_cls.fromisoformat(exit_date) - date_cls.fromisoformat(entry_date)).days
    pnl_pct = (exit_price - entry_price) / entry_price
    pnl_amount = (exit_price - entry_price) * LOT_SIZE
    return BacktestTradeResult(
        entry_date=entry_date,
        entry_price=entry_price,
        exit_date=exit_date,
        exit_price=exit_price,
        hold_days=hold_days,
        pnl_pct=pnl_pct,
        pnl_amount=pnl_amount,
        exit_reason=exit_reason,
    )


def _summarize(trades: list[BacktestTradeResult], min_trade_count: int) -> BacktestSummary:
    """Aggregate metrics over STOP_LOSS/TAKE_PROFIT trades only (design.md 7.4).

    ``PERIOD_END`` records are display-only and excluded from every metric here.
    """
    counted = [t for t in trades if t.exit_reason != EXIT_PERIOD_END]
    trade_count = len(counted)
    sample_insufficient = trade_count < min_trade_count

    if trade_count == 0:
        return BacktestSummary(
            win_rate=None,
            avg_win_loss_ratio=None,
            max_drawdown=None,
            trade_count=0,
            total_return=None,
            annual_return=None,
            sample_insufficient=sample_insufficient,
        )

    wins = [t for t in counted if t.pnl_pct > 0]
    losses = [t for t in counted if t.pnl_pct <= 0]
    win_rate = len(wins) / trade_count

    avg_win = (sum(t.pnl_pct for t in wins) / len(wins)) if wins else 0.0
    avg_loss = (abs(sum(t.pnl_pct for t in losses) / len(losses))) if losses else 0.0
    avg_win_loss_ratio = (avg_win / avg_loss) if avg_loss > 0 else None

    equity = 1.0
    peak = 1.0
    max_drawdown = 0.0
    for t in counted:
        equity *= 1.0 + t.pnl_pct
        peak = max(peak, equity)
        if peak > 0:
            max_drawdown = max(max_drawdown, (peak - equity) / peak)
    total_return = equity - 1.0

    span_days = (
        date_cls.fromisoformat(counted[-1].exit_date)
        - date_cls.fromisoformat(counted[0].entry_date)
    ).days
    annual_return = (
        (1.0 + total_return) ** (365.0 / span_days) - 1.0 if span_days > 0 else None
    )

    return BacktestSummary(
        win_rate=win_rate,
        avg_win_loss_ratio=avg_win_loss_ratio,
        max_drawdown=max_drawdown,
        trade_count=trade_count,
        total_return=total_return,
        annual_return=annual_return,
        sample_insufficient=sample_insufficient,
    )


def run_backtest(
    bars: list[dict],
    *,
    start_date: str,
    n: int,
    x: float,
    risk: RiskParams,
    min_trade_count: int = 5,
) -> BacktestResult:
    """Replay the buy rule + three-tier stop day by day over ``bars``.

    Args:
        bars: ALL daily bars needed, oldest-first, covering
            ``[start_date - >= n trading days, end_date]`` (the warm-up window
            must already be included so the first in-scope day has a full
            N-day low baseline). Each bar needs keys: date (ISO str), open,
            high, low, close.
        start_date: first trade date (ISO) that is in-scope for entries/exits;
            bars strictly before it are warm-up only and never traded.
        n: rolling window size (trading days) for the buy baseline (low_min).
        x: buy coefficient — enters when ``close <= low_min * x``.
        risk: three-tier stop params, shared with the live engine. Partial
            take-profit is forced off regardless of the caller's value — the
            backtest never simulates "分批止盈"/"持仓中加仓" (design.md 7.3).
        min_trade_count: sample_insufficient threshold, evaluated against the
            STOP_LOSS/TAKE_PROFIT trade count only (excludes PERIOD_END).

    Returns:
        BacktestResult with the trade list (at most one trailing PERIOD_END
        record) and summary metrics.
    """
    if n <= 0:
        raise ValueError("n must be positive")
    if x <= 0:
        raise ValueError("x must be positive")

    risk = replace(risk, enable_partial_take_profit=False)

    # Drop non-positive / structurally impossible / extreme-range / close-gap
    # bars (see sanitize_bars_for_backtest) instead of letting a bogus low
    # poison low_min or a bogus close feed avg_cost=0 into risk_rules.evaluate.
    cleaned = sanitize_bars_for_backtest(bars)
    if len(cleaned) != len(bars):
        logger.warning(
            "run_backtest: dropped %d bar(s) during OHLC sanitize out of %d",
            len(bars) - len(cleaned),
            len(bars),
        )
    bars = cleaned

    trades: list[BacktestTradeResult] = []

    qty = 0
    avg_cost = 0.0
    highest: float | None = None
    stop_price: float | None = None
    entry_date: str | None = None
    entry_price = 0.0
    last_bar_date: str | None = None

    in_scope_indices = [i for i, bar in enumerate(bars) if bar["date"] >= start_date]

    def _clear_position() -> None:
        nonlocal qty, avg_cost, highest, stop_price, entry_date, entry_price, last_bar_date
        qty = 0
        avg_cost = 0.0
        highest = None
        stop_price = None
        entry_date = None
        entry_price = 0.0
        last_bar_date = None

    for idx in in_scope_indices:
        bar = bars[idx]

        if qty > 0:
            assert (
                entry_date is not None
                and highest is not None
                and stop_price is not None
                and last_bar_date is not None
            )
            hold_gap = (
                date_cls.fromisoformat(bar["date"]) - date_cls.fromisoformat(last_bar_date)
            ).days
            if hold_gap > _MAX_BAR_GAP_CALENDAR_DAYS:
                # Data hole after sanitize: cannot manage risk across the gap.
                # Void the open position (no SL/TP/PERIOD_END row) and fall
                # through so this bar may still trigger a fresh buy.
                logger.warning(
                    "run_backtest: voiding open position from %s — calendar gap "
                    "%d days to %s exceeds %d",
                    entry_date,
                    hold_gap,
                    bar["date"],
                    _MAX_BAR_GAP_CALENDAR_DAYS,
                )
                _clear_position()
            else:
                # Holding: shared OHLC adapter (design.md 7.3).
                day = evaluate_bar_holding(
                    avg_cost=avg_cost,
                    highest_since_hold=highest,
                    prev_stop=stop_price,
                    open_price=float(bar["open"]),
                    high=float(bar["high"]),
                    low=float(bar["low"]),
                    params=risk,
                )
                highest = day.highest_since_hold
                stop_price = day.stop_price

                if day.exit_reason is not None and day.exit_price is not None:
                    trades.append(
                        _make_trade(
                            entry_date,
                            entry_price,
                            bar["date"],
                            day.exit_price,
                            day.exit_reason,
                        )
                    )
                    _clear_position()
                    continue

                last_bar_date = bar["date"]
                continue

        if qty == 0:
            # Full N-day warm-up required (design.md 7.2): never enter on a
            # partial window — a short window lets a single dirty low dominate.
            if idx < n:
                continue
            window = bars[idx - n : idx]
            # Baseline is only trustworthy when the N prior bars and today form
            # a continuous calendar sequence (no multi-month/year sanitize hole).
            window_dates = [b["date"] for b in window] + [bar["date"]]
            gap = _max_consecutive_gap_days(window_dates)
            if gap > _MAX_BAR_GAP_CALENDAR_DAYS:
                continue
            low_min, _high_max, actual_n = compute_baseline(window, n)
            if is_buy_signal(
                float(bar["close"]),
                low_min,
                x,
                actual_n=actual_n,
                n=n,
                require_full_n=True,
            ):
                qty = 1
                avg_cost = float(bar["close"])
                entry_date = bar["date"]
                entry_price = avg_cost
                initial = evaluate(
                    avg_cost=avg_cost,
                    highest_since_hold=avg_cost,
                    prev_stop=None,
                    price=avg_cost,
                    params=risk,
                )
                highest = initial.highest_since_hold
                stop_price = initial.stop_price
                last_bar_date = bar["date"]
            continue

    if qty > 0 and entry_date is not None and in_scope_indices:
        last_bar = bars[in_scope_indices[-1]]
        trades.append(
            _make_trade(
                entry_date,
                entry_price,
                last_bar["date"],
                float(last_bar["close"]),
                EXIT_PERIOD_END,
            )
        )

    summary = _summarize(trades, min_trade_count)
    return BacktestResult(trades=trades, summary=summary)
