"""Peak-valley buy/sell gated by a 7-day average volume confirmation."""

from __future__ import annotations

from app.market_signal.peak_valley import PeakValleyStrategy
from app.market_signal.strategy import (
    MarketSignalStrategy,
    SignalDecision,
    SignalRequest,
)

__all__ = ["PeakValueWithVolumeStrategy"]

_EPS = 1e-12


class PeakValueWithVolumeStrategy(MarketSignalStrategy):
    """Peak-valley price rule plus volume confirmation.

    Buy / addon only when current volume is at least
    ``avg_volume * (1 + buy_volume_increase_pct)`` versus the prior
    ``volume_lookback`` days. Sell only when the absolute volume change
    versus that average is at least ``sell_volume_change_pct``. Missing
    current volume or an incomplete lookback window fails confirmation.
    """

    def __init__(self, *, require_full_n: bool = False) -> None:
        self._inner = PeakValleyStrategy(require_full_n=require_full_n)

    @property
    def require_full_n(self) -> bool:
        return self._inner.require_full_n

    def evaluate(self, req: SignalRequest) -> SignalDecision:
        base = self._inner.evaluate(req)
        vol_ok_buy = self._volume_increased(req)
        vol_ok_sell = self._volume_changed(req)
        return SignalDecision(
            buy=base.buy and vol_ok_buy,
            sell=base.sell and vol_ok_sell,
            addon=base.addon and vol_ok_buy,
            buy_reference=base.buy_reference,
            sell_reference=base.sell_reference,
        )

    def _volume_change(self, req: SignalRequest) -> float | None:
        current = req.volume
        if current is None or float(current) <= 0:
            return None
        lookback = int(req.params.volume_lookback)
        if lookback <= 0:
            return None
        window = req.feed.volume_window(lookback)
        if window.actual_n < lookback or window.avg_volume <= 0:
            return None
        return float(current) / float(window.avg_volume) - 1.0

    def _volume_increased(self, req: SignalRequest) -> bool:
        change = self._volume_change(req)
        if change is None:
            return False
        return change + _EPS >= float(req.params.buy_volume_increase_pct)

    def _volume_changed(self, req: SignalRequest) -> bool:
        change = self._volume_change(req)
        if change is None:
            return False
        return abs(change) + _EPS >= float(req.params.sell_volume_change_pct)
