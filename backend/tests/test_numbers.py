"""Numeric parsers must not mix missing values with conversion errors."""

from __future__ import annotations

import math

import pandas as pd
import pytest

from app.services.market_data.base import build_daily_bar
from app.services.market_data.numbers import (
    coerce_optional_float,
    is_missing_number,
    parse_float,
    parse_optional_float,
)


@pytest.mark.parametrize(
    "value",
    [
        None,
        float("nan"),
        "",
        "  ",
        "nan",
        "NaN",
        "none",
        "-",
        "--",
        "n/a",
        pd.NA,
        pd.NaT,
    ],
)
def test_is_missing_number_accepts_vendor_placeholders(value: object) -> None:
    assert is_missing_number(value) is True


@pytest.mark.parametrize("value", [0, 0.0, 1, "1.5", False, True, "abc", [], {}])
def test_is_missing_number_rejects_present_values(value: object) -> None:
    assert is_missing_number(value) is False


def test_parse_float_rejects_missing_and_non_finite() -> None:
    with pytest.raises(ValueError, match="missing"):
        parse_float(None)
    with pytest.raises(ValueError, match="missing"):
        parse_float(float("nan"))
    with pytest.raises(TypeError, match="boolean"):
        parse_float(True)
    with pytest.raises(ValueError, match="invalid"):
        parse_float("abc")
    with pytest.raises(ValueError, match="non-finite"):
        parse_float("inf")
    assert parse_float("10.5") == 10.5
    assert parse_float(0) == 0.0


def test_parse_optional_float_none_is_normal_not_caught_error() -> None:
    assert parse_optional_float(None) is None
    assert parse_optional_float("") is None
    assert parse_optional_float(float("nan")) is None
    assert parse_optional_float("3.14") == pytest.approx(3.14)
    with pytest.raises(ValueError, match="invalid"):
        parse_optional_float("not-a-number")


def test_coerce_optional_float_keeps_missing_off_exception_path() -> None:
    assert coerce_optional_float(None) is None
    assert coerce_optional_float(float("nan")) is None
    assert coerce_optional_float("abc") is None
    assert coerce_optional_float("2.5") == 2.5
    assert coerce_optional_float(0) == 0.0
    assert not math.isnan(coerce_optional_float("1") or 0)


def test_build_daily_bar_skips_invalid_ohlc_keeps_invalid_turnover() -> None:
    skipped = build_daily_bar(
        date="2024-01-02",
        open_price=float("nan"),
        high_price=11.0,
        low_price=9.0,
        close_price=10.0,
        volume=1000,
        turnover_rate=1.0,
        has_turnover=True,
    )
    assert skipped is None

    kept = build_daily_bar(
        date="2024-01-02",
        open_price=10.0,
        high_price=11.0,
        low_price=9.0,
        close_price=10.5,
        volume=1000,
        turnover_rate="garbage",
        has_turnover=True,
    )
    assert kept is not None
    assert kept["close"] == 10.5
    assert kept["turnover_rate"] is None
