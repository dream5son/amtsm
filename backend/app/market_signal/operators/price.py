"""Price gate operators."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from app.market_signal.operators.base import OperatorVerdict
from app.market_signal.strategy import SignalReference, SignalRequest

_EPS = 1e-12


class PriceParams(BaseModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, strict=True, allow_inf_nan=False
    )

    lookback_days: int = Field(gt=0)
    factor: float = Field(gt=0)


class PriceNearLowParams(PriceParams):
    pass


class PriceNearHighParams(PriceParams):
    pass


def price_near_low(req: SignalRequest, params: BaseModel) -> OperatorVerdict:
    parsed = PriceNearLowParams.model_validate(params)
    window = req.feed.baseline()
    price = float(req.price)
    baseline = float(window.low_min)
    factor = float(parsed.factor)
    threshold = baseline * factor
    passed = price > 0 and baseline > 0 and price <= threshold + _EPS
    return OperatorVerdict(
        passed=passed,
        reference=SignalReference(baseline, factor),
        details={
            "price": price,
            "baseline_price": baseline,
            "factor": factor,
            "threshold": threshold,
            "lookback_days": parsed.lookback_days,
        },
    )


def price_near_high(req: SignalRequest, params: BaseModel) -> OperatorVerdict:
    parsed = PriceNearHighParams.model_validate(params)
    window = req.feed.baseline()
    price = float(req.price)
    baseline = float(window.high_max)
    factor = float(parsed.factor)
    threshold = baseline * factor
    passed = price > 0 and baseline > 0 and price >= threshold - _EPS
    return OperatorVerdict(
        passed=passed,
        reference=SignalReference(baseline, factor),
        details={
            "price": price,
            "baseline_price": baseline,
            "factor": factor,
            "threshold": threshold,
            "lookback_days": parsed.lookback_days,
        },
    )
