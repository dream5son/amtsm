"""Volume gate operators."""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field

from app.market_signal.operators.base import OperatorVerdict
from app.market_signal.strategy import SignalRequest

_EPS = 1e-12


class VolumeIncreaseParams(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, allow_inf_nan=False)

    lookback_days: int = Field(gt=0)
    min_change_pct: float = Field(ge=0)


class VolumeAbsChangeParams(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, allow_inf_nan=False)

    lookback_days: int = Field(gt=0)
    min_abs_change_pct: float = Field(ge=0)


@dataclass(frozen=True)
class _VolumeChange:
    change: float | None
    details: dict[str, float | int | None]


def _volume_change(req: SignalRequest, lookback_days: int) -> _VolumeChange:
    current = None if req.volume is None else float(req.volume)
    details: dict[str, float | int | None] = {
        "current_volume": current,
        "avg_volume": None,
        "actual_n": None,
        "lookback_days": lookback_days,
        "change_pct": None,
    }
    if current is None or current <= 0:
        return _VolumeChange(None, details)

    window = req.feed.volume_window(lookback_days)
    avg_volume = float(window.avg_volume)
    details["avg_volume"] = avg_volume
    details["actual_n"] = window.actual_n
    if window.actual_n < lookback_days or avg_volume <= 0:
        return _VolumeChange(None, details)

    change = current / avg_volume - 1.0
    details["change_pct"] = change
    return _VolumeChange(change, details)


def volume_increase(req: SignalRequest, params: BaseModel) -> OperatorVerdict:
    parsed = VolumeIncreaseParams.model_validate(params)
    threshold = float(parsed.min_change_pct)
    if threshold == 0:
        return OperatorVerdict(
            passed=True,
            details={
                "lookback_days": parsed.lookback_days,
                "min_change_pct": threshold,
                "identity": True,
            },
        )

    result = _volume_change(req, parsed.lookback_days)
    return OperatorVerdict(
        passed=result.change is not None and result.change + _EPS >= threshold,
        details={**result.details, "min_change_pct": threshold, "identity": False},
    )


def volume_abs_change(req: SignalRequest, params: BaseModel) -> OperatorVerdict:
    parsed = VolumeAbsChangeParams.model_validate(params)
    threshold = float(parsed.min_abs_change_pct)
    if threshold == 0:
        return OperatorVerdict(
            passed=True,
            details={
                "lookback_days": parsed.lookback_days,
                "min_abs_change_pct": threshold,
                "identity": True,
            },
        )

    result = _volume_change(req, parsed.lookback_days)
    return OperatorVerdict(
        passed=result.change is not None and abs(result.change) + _EPS >= threshold,
        details={
            **result.details,
            "min_abs_change_pct": threshold,
            "identity": False,
        },
    )
