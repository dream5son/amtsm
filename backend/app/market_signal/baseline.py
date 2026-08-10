"""N-day high/low baseline computation from daily bars."""

from __future__ import annotations


class InsufficientDataError(Exception):
    """Raised when there is zero usable data for computation."""


def compute_baseline(bars: list[dict], n: int) -> tuple[float, float, int]:
    """Compute the N-day low_min and high_max from daily bars.

    Args:
        bars: List of bar dicts (oldest first), must NOT include the current day.
        n: Desired number of days to use.

    Returns:
        Tuple of (low_min, high_max, actual_n) where actual_n <= n.

    Raises:
        InsufficientDataError: If bars is empty (K=0).
    """
    if not bars:
        raise InsufficientDataError("No historical data available for baseline computation")

    # Take the most recent N bars (bars are oldest-first)
    recent_bars = bars[-n:] if len(bars) >= n else bars
    actual_n = len(recent_bars)

    low_min = min(bar["low"] for bar in recent_bars)
    high_max = max(bar["high"] for bar in recent_bars)

    return low_min, high_max, actual_n
