"""Buy / sell / addon strategy contract shared by live engine and backtest.

Callers build one :class:`SignalRequest` — current price, resolved config, and
a :class:`~app.market_signal.feed.MarketFeed` — and read one
:class:`SignalDecision` back. Nothing in the request is strategy-shaped, so
swapping strategies never touches a call site.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from app.market_signal.feed import MarketFeed

STRATEGY_PEAK_VALLEY = "peak_valley"
STRATEGY_PEAK_VALUE_WITH_VOLUME = "peak_value_with_volume"

__all__ = [
    "STRATEGY_PEAK_VALLEY",
    "STRATEGY_PEAK_VALUE_WITH_VOLUME",
    "MarketSignalStrategy",
    "SignalDecision",
    "SignalReference",
    "SignalRequest",
    "StrategyParams",
    "get_market_signal_strategy",
]


@dataclass(frozen=True)
class StrategyParams:
    """Per-stock knobs resolved from global config plus overrides.

    Only user-configured values belong here; anything derived from market data
    is pulled from the feed instead.
    """

    x: float = 1.0  # 买入系数：买入阈值 = N 日低点 * x（如 1.1 表示低点上浮 10%）
    y: float = 1.0  # 卖出系数：卖出阈值 = N 日高点 * y（如 0.9 表示高点下浮 10%）
    volume_lookback: int = 7  # 量能确认回看交易日数（不含当日）
    buy_volume_increase_pct: float = 0.30  # 买入：当日量相对均量至少放量该比例
    sell_volume_change_pct: float = 0.30  # 卖出：当日量相对均量的绝对变化至少该比例


@dataclass(frozen=True)
class SignalRequest:
    """Uniform strategy input for one stock at one price."""

    price: float
    feed: MarketFeed
    params: StrategyParams = field(default_factory=StrategyParams)
    volume: float | None = None  # 当前时刻成交量（盘中累计 / 回测当日）


@dataclass(frozen=True)
class SignalReference:
    """The two numbers alert copy quotes: 「已触及近 N 日低点 X 的 1.10 倍」."""

    baseline_price: float
    used_coeff: float


@dataclass(frozen=True)
class SignalDecision:
    """All three technical verdicts for one price.

    Callers keep the ones their position state allows: empty positions act on
    ``buy`` / ``sell``, holdings on ``addon`` / ``sell``.
    """

    buy: bool
    sell: bool
    addon: bool
    buy_reference: SignalReference  # BUY 与 ADDON 共用
    sell_reference: SignalReference


class MarketSignalStrategy(ABC):
    """Technical entry/exit; risk stops live in :mod:`app.market_signal.risk`."""

    @abstractmethod
    def evaluate(self, req: SignalRequest) -> SignalDecision:
        """Judge buy / sell / addon for one stock at one price."""


def get_market_signal_strategy(
    name: str = STRATEGY_PEAK_VALUE_WITH_VOLUME,
    **kwargs: Any,
) -> MarketSignalStrategy:
    """Return a strategy instance by registry name.

    Extra ``kwargs`` are forwarded to the implementation constructor, which is
    where per-run policy lives (peak-valley accepts ``require_full_n``).
    """
    if name == STRATEGY_PEAK_VALLEY:
        from app.market_signal.peak_valley import PeakValleyStrategy

        return PeakValleyStrategy(**kwargs)
    if name == STRATEGY_PEAK_VALUE_WITH_VOLUME:
        from app.market_signal.peak_value_with_volume import PeakValueWithVolumeStrategy

        return PeakValueWithVolumeStrategy(**kwargs)
    raise ValueError(f"unknown market signal strategy: {name}")
