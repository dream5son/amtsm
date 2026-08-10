"""Baseline calculator - computes N-day high/low baselines from daily bars.

Compatibility re-export; implementation lives in :mod:`app.market_signal.baseline`.
"""

from app.market_signal.baseline import InsufficientDataError, compute_baseline

__all__ = ["InsufficientDataError", "compute_baseline"]
