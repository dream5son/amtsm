"""N-day high/low peak-valley buy/sell strategy."""

from __future__ import annotations

from app.market_signal.feed import BaselineWindow
from app.market_signal.strategy import (
    MarketSignalStrategy,
    SignalDecision,
    SignalReference,
    SignalRequest,
)

__all__ = ["PeakValleyStrategy"]

# 浮点运算误差容差，避免 price 与阈值在数学上相等却因精度差导致比较失败
_EPS = 1e-12


class PeakValleyStrategy(MarketSignalStrategy):
    """Buy near the N-day low, sell near the N-day high.

    Buy when ``price <= low_min * x``; sell when ``price >= high_max * y``.
    Addon reuses the buy threshold but never requires a full N-day window.

    ``require_full_n`` is a per-run caller policy (backtest enforces a complete
    warm-up window, live tolerates a degraded one), constant for the whole run,
    so it lives on the instance rather than on every request.
    """

    def __init__(self, *, require_full_n: bool = False) -> None:
        self.require_full_n = require_full_n

    def buy_threshold(self, low_min: float, x: float) -> float:
        return float(low_min) * float(x)

    def sell_threshold(self, high_max: float, y: float) -> float:
        return float(high_max) * float(y)

    def evaluate(self, req: SignalRequest) -> SignalDecision:
        window = req.feed.baseline()
        price = float(req.price)
        x = float(req.params.x)
        y = float(req.params.y)

        hits_buy = (
            price > 0
            and window.low_min > 0
            and x > 0
            and price <= self.buy_threshold(window.low_min, x) + _EPS
        )
        hits_sell = (
            price > 0
            and window.high_max > 0
            and y > 0
            and price >= self.sell_threshold(window.high_max, y) - _EPS
        )
        full_window = not self.require_full_n or self._is_full_window(window)

        return SignalDecision(
            buy=hits_buy and full_window,
            sell=hits_sell,
            addon=hits_buy,
            buy_reference=SignalReference(float(window.low_min), x),
            sell_reference=SignalReference(float(window.high_max), y),
        )

    def _is_full_window(self, window: BaselineWindow) -> bool:
        if window.actual_n is None or window.requested_n is None:
            raise ValueError(
                "actual_n and requested_n are required when require_full_n=True"
            )
        return window.actual_n >= window.requested_n
