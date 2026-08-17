"""Market data port sitting between callers and strategies.

The live engine serves values precomputed by the nightly baseline job; the
backtest derives them from its in-memory bar window. Strategies pull what they
need through :class:`MarketFeed`, so no caller has to know which strategy is
active. A future strategy needing a feature the live feed cannot serve must
have it added to the precompute job — this port makes that cost explicit
instead of letting each caller hand-build strategy-shaped inputs.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.market_signal.baseline import compute_baseline

DEFAULT_VOLUME_LOOKBACK = 7

__all__ = [
    "BarWindowFeed",
    "BaselineWindow",
    "DEFAULT_VOLUME_LOOKBACK",
    "MarketFeed",
    "StaticBaselineFeed",
    "VolumeWindow",
    "compute_volume_window",
]


@dataclass(frozen=True)
class BaselineWindow:
    """N-day low/high over one stock's lookback window.

    ``actual_n`` / ``requested_n`` are ``None`` when the caller cannot report
    them: the live cache stores only the computed extremes, not the N it was
    computed with, so window completeness is simply unknowable intraday.
    """

    low_min: float = 0.0  # 窗口内最低价
    high_max: float = 0.0  # 窗口内最高价
    actual_n: int | None = None  # 实际参与计算的 K 线天数，历史不足时小于 requested_n
    requested_n: int | None = None  # 配置要求的 N 日窗口


@dataclass(frozen=True)
class VolumeWindow:
    """Average volume over the lookback bars that exclude the evaluated day."""

    avg_volume: float = 0.0
    actual_n: int = 0  # 实际参与均量的 K 线天数，历史不足时小于 lookback
    lookback: int = DEFAULT_VOLUME_LOOKBACK


def compute_volume_window(
    bars: list[dict], lookback: int = DEFAULT_VOLUME_LOOKBACK
) -> VolumeWindow:
    """Average positive volume over the most recent ``lookback`` bars.

    ``bars`` are oldest-first and must exclude the bar being evaluated.
    Bars with missing or non-positive volume are skipped, so ``actual_n``
    may be smaller than ``lookback``.
    """
    if lookback <= 0 or not bars:
        return VolumeWindow(avg_volume=0.0, actual_n=0, lookback=lookback)
    recent = bars[-lookback:]
    volumes = [
        float(bar["volume"])
        for bar in recent
        if bar.get("volume") is not None and float(bar["volume"]) > 0
    ]
    actual_n = len(volumes)
    avg = sum(volumes) / actual_n if actual_n else 0.0
    return VolumeWindow(avg_volume=avg, actual_n=actual_n, lookback=lookback)


class MarketFeed(Protocol):
    """Derived market data for one stock at one evaluation moment."""

    def baseline(self) -> BaselineWindow:
        ...

    def volume_window(self, lookback: int = DEFAULT_VOLUME_LOOKBACK) -> VolumeWindow:
        ...


class StaticBaselineFeed:
    """Feed over an already-computed baseline (live nightly precompute)."""

    def __init__(
        self,
        window: BaselineWindow,
        volume: VolumeWindow | None = None,
    ) -> None:
        self._window = window
        self._volume = volume if volume is not None else VolumeWindow()

    def baseline(self) -> BaselineWindow:
        return self._window

    def volume_window(self, lookback: int = DEFAULT_VOLUME_LOOKBACK) -> VolumeWindow:
        return self._volume


class BarWindowFeed:
    """Feed that derives indicators from an in-memory bar window (backtest).

    Bars are oldest-first and must exclude the bar being evaluated.
    """

    def __init__(self, bars: list[dict], *, requested_n: int) -> None:
        self._bars = bars
        self._requested_n = requested_n
        self._cached: BaselineWindow | None = None
        self._cached_volume: dict[int, VolumeWindow] = {}

    def baseline(self) -> BaselineWindow:
        if self._cached is None:
            low_min, high_max, actual_n = compute_baseline(self._bars, self._requested_n)
            self._cached = BaselineWindow(
                low_min=low_min,
                high_max=high_max,
                actual_n=actual_n,
                requested_n=self._requested_n,
            )
        return self._cached

    def volume_window(self, lookback: int = DEFAULT_VOLUME_LOOKBACK) -> VolumeWindow:
        if lookback not in self._cached_volume:
            self._cached_volume[lookback] = compute_volume_window(self._bars, lookback)
        return self._cached_volume[lookback]
