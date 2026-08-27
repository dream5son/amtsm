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
    SIGNAL_PERIOD_END,
    SIGNAL_STOP_LOSS,
    SIGNAL_TAKE_PROFIT,
    BarWindowFeed,
    FeedRequirements,
    MarketSignalStrategy,
    RiskParams,
    SignalRequest,
    StrategyParams,
    evaluate,
    evaluate_bar_holding,
    get_market_signal_strategy,
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


def is_comparable_bar(bar: dict) -> bool:
    """True when OHLC is usable as a traded price on today's qfq basis."""
    return has_valid_ohlc(bar["open"], bar["high"], bar["low"], bar["close"])


def has_valid_ohlc(
    open_price: float, high_price: float, low_price: float, close_price: float
) -> bool:
    """True when a bar looks like a tradable daily candle.

    Rejects:
    - zero/negative OHLC (halted-day glitches; early qfq going negative)
    - structurally impossible OHLC (low above body, high below body)
    - extreme intraday range that is almost always a forward-adjustment
      artifact rather than a real session (poisons N-day low_min)

    Shared by backtest replay (as an incomparable mask, not a delete) and the
    kline display so the chart never shows a candle the simulation would skip.
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


# Minimum overnight open gap (vs prior close) that suggests an unadjusted
# ex-rights / ex-dividend discontinuity rather than a real session move.
_UNADJUSTED_OVERNIGHT_GAP_PCT = 0.20

# Overnight-gap checks only apply across a short calendar hole (weekend /
# 春节 ~11d). Longer holes are stock-specific halts (股改停牌 is typically
# 2+ weeks) and must not trip the unadjusted-series safety net.
_MAX_UNADJUSTED_GAP_CALENDAR_DAYS = 13


def _is_halt_placeholder(bar: dict) -> bool:
    """True when the row is a zero-volume halt fill, not a traded session."""
    raw = bar.get("volume")
    if raw is None:
        return False
    return float(raw) <= 0


def detect_unadjusted_corporate_actions(bars: list[dict]) -> list[str]:
    """Return ISO dates where overnight gaps look like unadjusted corporate actions.

    A large drop from ``prev.close`` to ``curr.open`` with a normal intraday
    range (not an extreme-range dirty qfq bar) is the signature of a bonus
    issue or dividend paid on an unadjusted series — e.g. sh600900 2010-07-20.

    Halt-resume gaps are excluded. A-share 股改 (e.g. sz000651 2006-03-08)
    often copies the last close into zero-volume placeholder bars, then gaps
    ~20%+ on resume; that is a real session after a halt, not an unadjusted
    10送转. The same skip applies when the provider omits the halt dates
    (calendar hole longer than 春节).
    """
    anomalies: list[str] = []
    prev: dict | None = None
    halt_since_prev = False
    for bar in bars:
        if not is_comparable_bar(bar):
            continue
        if _is_halt_placeholder(bar):
            halt_since_prev = True
            continue
        if prev is not None and not halt_since_prev:
            prev_close = float(prev["close"])
            curr_open = float(bar["open"])
            close = float(bar["close"])
            calendar_gap = (
                date_cls.fromisoformat(str(bar["date"]))
                - date_cls.fromisoformat(str(prev["date"]))
            ).days
            if prev_close > 0 and calendar_gap <= _MAX_UNADJUSTED_GAP_CALENDAR_DAYS:
                overnight_gap = (prev_close - curr_open) / prev_close
                intraday_range = (float(bar["high"]) - float(bar["low"])) / close
                if (
                    overnight_gap > _UNADJUSTED_OVERNIGHT_GAP_PCT + 1e-12
                    and intraday_range <= _MAX_INTRADAY_RANGE_PCT + 1e-12
                ):
                    anomalies.append(str(bar["date"]))
        prev = bar
        halt_since_prev = False
    return anomalies


def validate_bar_series(bars: list[dict]) -> None:
    """Raise ValueError when cached bars look like an unadjusted OHLC series."""
    anomalies = detect_unadjusted_corporate_actions(bars)
    if anomalies:
        sample = ", ".join(anomalies[:5])
        suffix = "..." if len(anomalies) > 5 else ""
        raise ValueError(
            f"suspected unadjusted corporate-action gaps on: {sample}{suffix}"
        )


def sanitize_bars_for_backtest(bars: list[dict]) -> list[dict]:
    """Drop incomparable bars for kline display, preserving oldest-first order.

    The replay engine must NOT use this as a delete filter: removing rows
    creates calendar holes that void positions. Chart rendering still omits
    candles the simulation would refuse to trade.

    A sequential close-gap filter was tried and rejected: dropping a dirty
    bar while keeping the previous close as the gap reference causes a
    cascade that wipes most of a long qfq history (e.g. 福耀玻璃 6522 → 69
    bars), falsely triggering sample_insufficient.
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
    effective_start_date: str | None = None
    qfq_prefix_skipped: int = 0


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
    hold_days = (
        date_cls.fromisoformat(exit_date) - date_cls.fromisoformat(entry_date)
    ).days
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


def _summarize(
    trades: list[BacktestTradeResult],
    min_trade_count: int,
    *,
    effective_start_date: str | None = None,
    qfq_prefix_skipped: int = 0,
) -> BacktestSummary:
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
            effective_start_date=effective_start_date,
            qfq_prefix_skipped=qfq_prefix_skipped,
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
        effective_start_date=effective_start_date,
        qfq_prefix_skipped=qfq_prefix_skipped,
    )


def run_backtest(
    bars: list[dict],
    *,
    start_date: str,
    risk: RiskParams,
    n: int | None = None,
    x: float | None = None,
    strategy: MarketSignalStrategy | None = None,
    requirements: FeedRequirements | None = None,
    min_trade_count: int = 5,
) -> BacktestResult:
    """Replay the buy rule + three-tier stop day by day over ``bars``.

    Args:
        bars: ALL daily bars needed, oldest-first, covering
            ``[start_date - >= n trading days, end_date]`` (the warm-up window
            must already be included so the first in-scope day has a full
            N-day low baseline). Each bar needs keys: date (ISO str), open,
            high, low, close, and volume (used by the default volume-gated
            strategy; missing/non-positive volume blocks entries).
        start_date: first trade date (ISO) that is in-scope for entries/exits;
            bars strictly before it are warm-up only and never traded.
        n: legacy rolling window size used when ``strategy`` is omitted.
        x: legacy buy coefficient used when ``strategy`` is omitted.
        strategy: compiled recipe strategy. Its feed requirements determine the
            price and volume warm-up windows.
        requirements: feed requirements for ``strategy``.
        risk: three-tier stop params, shared with the live engine. Partial
            take-profit is forced off regardless of the caller's value — the
            backtest never simulates "分批止盈"/"持仓中加仓" (design.md 7.3).
        min_trade_count: sample_insufficient threshold, evaluated against the
            STOP_LOSS/TAKE_PROFIT trade count only (excludes PERIOD_END).

    Returns:
        BacktestResult with the trade list (at most one trailing PERIOD_END
        record) and summary metrics.
    """
    risk = replace(risk, enable_partial_take_profit=False)
    if strategy is None:
        if n is None or n <= 0:
            raise ValueError("n must be positive")
        if x is None or x <= 0:
            raise ValueError("x must be positive")
        strategy = get_market_signal_strategy(require_full_n=True)
        params = StrategyParams(x=x)
        feed_n = max(n, int(params.volume_lookback))
    else:
        if requirements is None:
            requirements = getattr(strategy, "requirements", None)
        if requirements is None:
            raise ValueError("requirements are required with strategy")
        n = int(requirements.price_lookback_days)
        if n <= 0:
            raise ValueError("price lookback must be positive")
        params = StrategyParams()
        feed_n = max(n, int(requirements.volume_lookback_days or 0))

    # Keep every row. Non-positive / extreme-range qfq is incomparable — skip
    # trading that day (and void an open position) instead of deleting the bar
    # and punching a calendar hole that cascades into sample_insufficient.
    comparable = [is_comparable_bar(bar) for bar in bars]
    incomparable_count = sum(0 if ok else 1 for ok in comparable)
    if incomparable_count:
        logger.warning(
            "run_backtest: masking %d incomparable bar(s) out of %d (kept in series)",
            incomparable_count,
            len(bars),
        )

    first_comparable_idx = next((i for i, ok in enumerate(comparable) if ok), None)
    prefix_skipped = (
        first_comparable_idx if first_comparable_idx is not None else len(bars)
    )
    first_comparable_date = (
        bars[first_comparable_idx]["date"] if first_comparable_idx is not None else None
    )
    effective_start = start_date
    if first_comparable_date is not None and first_comparable_date > start_date:
        effective_start = first_comparable_date

    trades: list[BacktestTradeResult] = []

    qty = 0
    avg_cost = 0.0
    highest: float | None = None
    stop_price: float | None = None
    entry_date: str | None = None
    entry_price = 0.0
    last_bar_date: str | None = None

    in_scope_indices = [
        i for i, bar in enumerate(bars) if bar["date"] >= effective_start
    ]

    def _clear_position() -> None:
        nonlocal \
            qty, \
            avg_cost, \
            highest, \
            stop_price, \
            entry_date, \
            entry_price, \
            last_bar_date
        qty = 0
        avg_cost = 0.0
        highest = None
        stop_price = None
        entry_date = None
        entry_price = 0.0
        last_bar_date = None

    for idx in in_scope_indices:
        bar = bars[idx]
        if not comparable[idx]:
            if qty > 0:
                logger.warning(
                    "run_backtest: voiding open position from %s — incomparable "
                    "qfq bar on %s (non-positive or extreme-range OHLC)",
                    entry_date,
                    bar["date"],
                )
                _clear_position()
            continue

        if qty > 0:
            assert (
                entry_date is not None
                and highest is not None
                and stop_price is not None
                and last_bar_date is not None
            )
            hold_gap = (
                date_cls.fromisoformat(bar["date"])
                - date_cls.fromisoformat(last_bar_date)
            ).days
            if hold_gap > _MAX_BAR_GAP_CALENDAR_DAYS:
                # True halt / missing rows: cannot manage risk across the gap.
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
            comparable_before = [bars[j] for j in range(idx) if comparable[j]]
            # Full N-day warm-up required (design.md 7.2): never enter on a
            # partial window — a short window lets a single dirty low dominate.
            if len(comparable_before) < n:
                continue
            window = comparable_before[-feed_n:]
            # Baseline is only trustworthy when the N prior comparable bars and
            # today form a continuous calendar sequence (no multi-month halt).
            window_dates = [b["date"] for b in window[-n:]] + [bar["date"]]
            gap = _max_consecutive_gap_days(window_dates)
            if gap > _MAX_BAR_GAP_CALENDAR_DAYS:
                continue
            raw_volume = bar.get("volume")
            volume = float(raw_volume) if raw_volume is not None else None
            decision = strategy.evaluate(
                SignalRequest(
                    price=float(bar["close"]),
                    volume=volume,
                    feed=BarWindowFeed(window, requested_n=n),
                    params=params,
                )
            )
            if decision.buy:
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

    if qty > 0 and entry_date is not None:
        last_comparable = next(
            (bars[i] for i in reversed(in_scope_indices) if comparable[i]),
            None,
        )
        if last_comparable is not None:
            trades.append(
                _make_trade(
                    entry_date,
                    entry_price,
                    last_comparable["date"],
                    float(last_comparable["close"]),
                    EXIT_PERIOD_END,
                )
            )

    summary = _summarize(
        trades,
        min_trade_count,
        effective_start_date=effective_start if bars else None,
        qfq_prefix_skipped=prefix_skipped,
    )
    return BacktestResult(trades=trades, summary=summary)
