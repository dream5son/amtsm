"""Daily-bar holding-day adapter for backtest (design.md 7.3).

Uses the day's high to ratchet highest/stop via :func:`evaluate`, then tests
the day's low against the updated stop. Fill price is ``min(open, stop_price)``.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.market_signal.risk import RiskEvaluation, RiskParams, evaluate
from app.market_signal.types import SIGNAL_STOP_LOSS, SIGNAL_TAKE_PROFIT


@dataclass(frozen=True)
class BarHoldingResult:
    highest_since_hold: float
    stop_price: float
    pnl_pct: float
    ladder_idx: int | None
    exit_reason: str | None
    exit_price: float | None
    risk_update: RiskEvaluation


def evaluate_bar_holding(
    *,
    avg_cost: float,
    highest_since_hold: float,
    prev_stop: float,
    open_price: float,
    high: float,
    low: float,
    params: RiskParams,
) -> BarHoldingResult:
    """One holding day on an OHLC bar.

    1. Update highest / stop with ``high``.
    2. If ``low <= stop_price``, exit at ``min(open, stop_price)``.
    3. Classify ``STOP_LOSS`` vs ``TAKE_PROFIT`` by comparing fill to ``avg_cost``.
    """
    updated = evaluate(
        avg_cost=avg_cost,
        highest_since_hold=highest_since_hold,
        prev_stop=prev_stop,
        price=float(high),
        params=params,
    )
    stop_price = updated.stop_price
    exit_reason: str | None = None
    exit_price: float | None = None

    if float(low) <= stop_price + 1e-12:
        exit_price = min(float(open_price), stop_price)
        exit_reason = (
            SIGNAL_STOP_LOSS
            if exit_price < avg_cost - 1e-12
            else SIGNAL_TAKE_PROFIT
        )

    return BarHoldingResult(
        highest_since_hold=updated.highest_since_hold,
        stop_price=stop_price,
        pnl_pct=updated.pnl_pct,
        ladder_idx=updated.ladder_idx,
        exit_reason=exit_reason,
        exit_price=exit_price,
        risk_update=updated,
    )
