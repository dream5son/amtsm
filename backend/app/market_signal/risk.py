"""Three-tier stop / take-profit rules shared by live engine and backtest."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from app.schemas.strategy import (
    PARTIAL_TP_SUGGEST_RATIOS,
    TrailingLadderLevel,
    parse_trailing_ladder,
)
from app.market_signal.types import (
    SIGNAL_PARTIAL_TP,
    SIGNAL_STOP_LOSS,
    SIGNAL_TAKE_PROFIT,
)

# Re-export for callers that import signal constants from risk.
__all__ = [
    "SIGNAL_STOP_LOSS",
    "SIGNAL_TAKE_PROFIT",
    "SIGNAL_PARTIAL_TP",
    "RiskParams",
    "PartialTpSignal",
    "RiskEvaluation",
    "match_ladder_idx",
    "suggest_sell_pct_for_ladder",
    "evaluate",
    "risk_params_from_resolved",
]


@dataclass(frozen=True)
class RiskParams:
    stop_loss_pct: float
    break_even_trigger_pct: float
    break_even_buffer_pct: float
    trailing_ladder: Sequence[TrailingLadderLevel]
    enable_partial_take_profit: bool = False


@dataclass(frozen=True)
class PartialTpSignal:
    ladder_idx: int
    pnl_pct: float
    suggest_sell_pct: float


@dataclass(frozen=True)
class RiskEvaluation:
    highest_since_hold: float
    stop_price: float
    pnl_pct: float
    ladder_idx: int | None
    exit_signal: str | None
    partial_tp: PartialTpSignal | None


def match_ladder_idx(
    pnl_pct: float, ladder: Sequence[TrailingLadderLevel]
) -> int | None:
    """Return ladder band index for trailing / partial-TP, or None if below first band."""
    for idx, level in enumerate(ladder):
        if pnl_pct < level.min_pnl - 1e-12:
            continue
        if level.max_pnl is None or pnl_pct < level.max_pnl - 1e-12:
            return idx
        # Exactly on max boundary: belong to next band if any, else this open-ended style.
        if abs(pnl_pct - level.max_pnl) < 1e-12:
            if idx + 1 < len(ladder):
                continue
            return idx
    return None


def suggest_sell_pct_for_ladder(ladder_idx: int) -> float:
    if ladder_idx < 0:
        return PARTIAL_TP_SUGGEST_RATIOS[0]
    if ladder_idx >= len(PARTIAL_TP_SUGGEST_RATIOS):
        return PARTIAL_TP_SUGGEST_RATIOS[-1]
    return PARTIAL_TP_SUGGEST_RATIOS[ladder_idx]


def evaluate(
    *,
    avg_cost: float,
    highest_since_hold: float | None,
    prev_stop: float | None,
    price: float,
    params: RiskParams,
    prev_ladder_idx: int | None = None,
) -> RiskEvaluation:
    """One evaluation cycle of the three-tier stop + optional partial TP."""
    if avg_cost <= 0:
        raise ValueError("avg_cost must be positive")
    if price <= 0:
        raise ValueError("price must be positive")

    highest = max(highest_since_hold or price, price)
    pnl_pct = (price - avg_cost) / avg_cost

    stop0 = avg_cost * (1.0 - params.stop_loss_pct)
    candidates = [prev_stop or 0.0, stop0]

    if pnl_pct >= params.break_even_trigger_pct - 1e-12:
        stop1 = avg_cost * (1.0 + params.break_even_buffer_pct)
        candidates.append(stop1)

    ladder_idx = match_ladder_idx(pnl_pct, params.trailing_ladder)
    if ladder_idx is not None:
        level = params.trailing_ladder[ladder_idx]
        stop2 = highest * (1.0 - level.drawdown)
        candidates.append(stop2)

    stop_price = max(candidates)

    exit_signal: str | None = None
    if price <= stop_price + 1e-12:
        exit_signal = SIGNAL_STOP_LOSS if price < avg_cost - 1e-12 else SIGNAL_TAKE_PROFIT

    partial_tp: PartialTpSignal | None = None
    if params.enable_partial_take_profit and ladder_idx is not None:
        prev = -1 if prev_ladder_idx is None else prev_ladder_idx
        if ladder_idx > prev:
            partial_tp = PartialTpSignal(
                ladder_idx=ladder_idx,
                pnl_pct=pnl_pct,
                suggest_sell_pct=suggest_sell_pct_for_ladder(ladder_idx),
            )

    return RiskEvaluation(
        highest_since_hold=highest,
        stop_price=stop_price,
        pnl_pct=pnl_pct,
        ladder_idx=ladder_idx,
        exit_signal=exit_signal,
        partial_tp=partial_tp,
    )


def risk_params_from_resolved(resolved: dict) -> RiskParams:
    ladder = resolved.get("trailing_ladder")
    if isinstance(ladder, list) and ladder and isinstance(ladder[0], TrailingLadderLevel):
        levels = ladder
    else:
        levels = parse_trailing_ladder(ladder)
    return RiskParams(
        stop_loss_pct=float(resolved["stop_loss_pct"]),
        break_even_trigger_pct=float(resolved["break_even_trigger_pct"]),
        break_even_buffer_pct=float(resolved["break_even_buffer_pct"]),
        trailing_ladder=levels,
        enable_partial_take_profit=bool(resolved.get("enable_partial_take_profit")),
    )
