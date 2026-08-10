"""Shared signal domain: baseline, entry rules, three-tier risk, day-bar replay."""

from app.market_signal.baseline import InsufficientDataError, compute_baseline
from app.market_signal.entry import (
    buy_threshold,
    is_addon_signal,
    is_buy_signal,
    is_tech_sell_signal,
    sell_threshold,
)
from app.market_signal.replay import BarHoldingResult, evaluate_bar_holding
from app.market_signal.risk import (
    PartialTpSignal,
    RiskEvaluation,
    RiskParams,
    evaluate,
    match_ladder_idx,
    risk_params_from_resolved,
    suggest_sell_pct_for_ladder,
)
from app.market_signal.types import (
    SIGNAL_ADDON,
    SIGNAL_BUY,
    SIGNAL_PARTIAL_TP,
    SIGNAL_PERIOD_END,
    SIGNAL_SELL,
    SIGNAL_STOP_LOSS,
    SIGNAL_TAKE_PROFIT,
    BaselineResult,
)

__all__ = [
    "BaselineResult",
    "BarHoldingResult",
    "InsufficientDataError",
    "PartialTpSignal",
    "RiskEvaluation",
    "RiskParams",
    "SIGNAL_ADDON",
    "SIGNAL_BUY",
    "SIGNAL_PARTIAL_TP",
    "SIGNAL_PERIOD_END",
    "SIGNAL_SELL",
    "SIGNAL_STOP_LOSS",
    "SIGNAL_TAKE_PROFIT",
    "buy_threshold",
    "compute_baseline",
    "evaluate",
    "evaluate_bar_holding",
    "is_addon_signal",
    "is_buy_signal",
    "is_tech_sell_signal",
    "match_ladder_idx",
    "risk_params_from_resolved",
    "sell_threshold",
    "suggest_sell_pct_for_ladder",
]
