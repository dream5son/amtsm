"""Public operator registry contracts."""

from app.market_signal.operators.base import OperatorSpec, OperatorVerdict
from app.market_signal.operators.registry import DEFAULT_OPERATOR_REGISTRY

__all__ = [
    "DEFAULT_OPERATOR_REGISTRY",
    "OperatorSpec",
    "OperatorVerdict",
]
