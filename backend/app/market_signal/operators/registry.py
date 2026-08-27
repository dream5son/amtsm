"""Closed V1 operator registry."""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

from app.market_signal.operators.base import OperatorSpec
from app.market_signal.operators.price import (
    PriceNearHighParams,
    PriceNearLowParams,
    price_near_high,
    price_near_low,
)
from app.market_signal.operators.volume import (
    VolumeAbsChangeParams,
    VolumeIncreaseParams,
    volume_abs_change,
    volume_increase,
)

_SPECS = (
    OperatorSpec(
        op="price_near_low",
        version=1,
        params_model=PriceNearLowParams,
        channels=frozenset({"buy", "addon"}),
        evaluator=price_near_low,
    ),
    OperatorSpec(
        op="price_near_high",
        version=1,
        params_model=PriceNearHighParams,
        channels=frozenset({"sell"}),
        evaluator=price_near_high,
    ),
    OperatorSpec(
        op="volume_increase",
        version=1,
        params_model=VolumeIncreaseParams,
        channels=frozenset({"buy", "sell", "addon"}),
        evaluator=volume_increase,
    ),
    OperatorSpec(
        op="volume_abs_change",
        version=1,
        params_model=VolumeAbsChangeParams,
        channels=frozenset({"buy", "sell", "addon"}),
        evaluator=volume_abs_change,
    ),
)

DEFAULT_OPERATOR_REGISTRY: Mapping[str, OperatorSpec] = MappingProxyType(
    {spec.key: spec for spec in _SPECS}
)
