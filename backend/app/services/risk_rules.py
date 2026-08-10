"""Pure risk-control rules shared by live engine and backtest.

Compatibility re-export; implementation lives in :mod:`app.market_signal.risk`.
"""

from app.market_signal.risk import (
    SIGNAL_PARTIAL_TP,
    SIGNAL_STOP_LOSS,
    SIGNAL_TAKE_PROFIT,
    PartialTpSignal,
    RiskEvaluation,
    RiskParams,
    evaluate,
    match_ladder_idx,
    risk_params_from_resolved,
    suggest_sell_pct_for_ladder,
)

__all__ = [
    "SIGNAL_PARTIAL_TP",
    "SIGNAL_STOP_LOSS",
    "SIGNAL_TAKE_PROFIT",
    "PartialTpSignal",
    "RiskEvaluation",
    "RiskParams",
    "evaluate",
    "match_ladder_idx",
    "risk_params_from_resolved",
    "suggest_sell_pct_for_ladder",
]
