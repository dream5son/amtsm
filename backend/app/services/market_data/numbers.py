"""Numeric parsing that keeps missing values off the exception path.

Missing / NaN / blank vendor tokens are a normal ``None``. Conversion of a
present but invalid value is a ``ValueError``. Callers that must tolerate dirty
optional fields use :func:`coerce_optional_float` and must not treat that
``None`` as a halt / zero / error signal.
"""

from __future__ import annotations

import math
from typing import Final

_MISSING_TOKENS: Final[frozenset[str]] = frozenset(
    {
        "",
        "nan",
        "none",
        "null",
        "nat",
        "-",
        "--",
        "n/a",
        "na",
    }
)


def is_missing_number(value: object) -> bool:
    """Return True for None, NaN, pandas NA, and blank vendor placeholders.

    Does not raise: array-like ``isna`` / ``!=`` results are treated as "not a
    scalar missing value" rather than conversion failure.
    """
    if value is None:
        return True
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return False
    if isinstance(value, float):
        return math.isnan(value)
    if isinstance(value, str):
        return value.strip().lower() in _MISSING_TOKENS
    type_name = type(value).__name__
    if type_name in {"NAType", "NaTType"}:
        return True
    isna = getattr(value, "isna", None)
    if callable(isna):
        try:
            flag = isna()
        except (TypeError, ValueError):
            return False
        return flag is True
    return False


def parse_float(value: object) -> float:
    """Parse a required finite number.

    Raises:
        TypeError: boolean values (``bool`` is not a market number).
        ValueError: missing, non-numeric, NaN, or Inf.
    """
    if isinstance(value, bool):
        raise TypeError("boolean is not a number")
    if is_missing_number(value):
        raise ValueError("missing number")
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid number: {value!r}") from exc
    if math.isnan(number) or math.isinf(number):
        raise ValueError(f"non-finite number: {value!r}")
    return number


def parse_optional_float(value: object) -> float | None:
    """Parse an optional number: missing → None; present invalid → ValueError."""
    if is_missing_number(value):
        return None
    return parse_float(value)


def coerce_optional_float(value: object) -> float | None:
    """Best-effort optional number for dirty vendor cells.

    Missing is a normal ``None`` (no try/except). Only ``float()`` conversion
    of a *present* value is caught; invalid tokens also become ``None``.
    """
    if is_missing_number(value):
        return None
    try:
        return parse_float(value)
    except (TypeError, ValueError):
        return None
