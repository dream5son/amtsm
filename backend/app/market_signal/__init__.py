"""Shared signal domain: baseline, entry strategy, three-tier risk, day-bar replay."""

from app.market_signal.baseline import InsufficientDataError, compute_baseline
from app.market_signal.feed import (
    BarWindowFeed,
    BaselineWindow,
    DEFAULT_VOLUME_LOOKBACK,
    MarketFeed,
    StaticBaselineFeed,
    VolumeWindow,
    compute_volume_window,
)
from app.market_signal.peak_valley import PeakValleyStrategy
from app.market_signal.peak_value_with_volume import PeakValueWithVolumeStrategy
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
from app.market_signal.strategy import (
    STRATEGY_PEAK_VALLEY,
    STRATEGY_PEAK_VALUE_WITH_VOLUME,
    MarketSignalStrategy,
    SignalDecision,
    SignalReference,
    SignalRequest,
    StrategyParams,
    get_market_signal_strategy,
)
from app.market_signal.types import (
    SIGNAL_ADDON,
    SIGNAL_BUY,
    SIGNAL_PARTIAL_TP,
    SIGNAL_PERIOD_END,
    SIGNAL_SELL,
    SIGNAL_STOP_LOSS,
    SIGNAL_TAKE_PROFIT,
)

__all__ = [
    "SIGNAL_ADDON",
    "SIGNAL_BUY",
    "SIGNAL_PARTIAL_TP",
    "SIGNAL_PERIOD_END",
    "SIGNAL_SELL",
    "SIGNAL_STOP_LOSS",
    "SIGNAL_TAKE_PROFIT",
    "STRATEGY_PEAK_VALLEY",
    "STRATEGY_PEAK_VALUE_WITH_VOLUME",
    "BarHoldingResult",
    "BarWindowFeed",
    "BaselineWindow",
    "DEFAULT_VOLUME_LOOKBACK",
    "InsufficientDataError",
    "MarketFeed",
    "MarketSignalStrategy",
    "PartialTpSignal",
    "PeakValleyStrategy",
    "PeakValueWithVolumeStrategy",
    "VolumeWindow",
    "RiskEvaluation",
    "RiskParams",
    "SignalDecision",
    "SignalReference",
    "SignalRequest",
    "StaticBaselineFeed",
    "StrategyParams",
    "compute_baseline",
    "compute_volume_window",
    "evaluate",
    "evaluate_bar_holding",
    "get_market_signal_strategy",
    "match_ladder_idx",
    "risk_params_from_resolved",
    "suggest_sell_pct_for_ladder",
]
