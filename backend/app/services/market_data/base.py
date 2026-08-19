"""Market data provider ABC, shared types, and exceptions."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date
from typing import TypedDict

from app.services.market_data.numbers import (
    coerce_optional_float,
    parse_float,
)

QFQ = "qfq"
UNADJUSTED = ""


class DailyBar(TypedDict):
    date: str
    open: float
    high: float
    low: float
    close: float
    volume: float
    turnover_rate: float | None


class RealtimeQuote(TypedDict):
    stock_name: str
    price: float | None
    open: float | None
    prev_close: float | None
    high: float | None
    low: float | None
    volume: float | None
    quote_date: str | None
    quote_time: str | None
    is_halted: bool
    has_quote: bool


@dataclass(slots=True)
class StockMeta:
    stock_code: str
    stock_name: str
    exchange: str
    short_code: str
    initials: str


class MarketDataUnavailableError(Exception):
    """Raised when the market data source is entirely unavailable."""


class StockDataFetchError(Exception):
    """Raised when fetching data for a specific stock fails."""


class MissingTradeDayBarError(StockDataFetchError):
    """Raised when OHLCV for the requested trade date is absent."""


def bar_date_str(value: object) -> str:
    """Normalise a date-like value to ``YYYY-MM-DD``."""
    return str(value).strip()[:10]


def build_daily_bar(
    *,
    date: object,
    open_price: object,
    high_price: object,
    low_price: object,
    close_price: object,
    volume: object,
    turnover_rate: object | None = None,
    has_turnover: bool = False,
) -> DailyBar | None:
    """Build one bar, or ``None`` when a required OHLC field is missing/invalid.

    Invalid optional turnover is coerced to ``None`` and does not drop the bar.
    """
    if date is None:
        return None
    date_str = bar_date_str(date)
    if len(date_str) < 10 or not date_str[0].isdigit():
        return None
    try:
        bar: DailyBar = {
            "date": date_str,
            "open": parse_float(open_price),
            "high": parse_float(high_price),
            "low": parse_float(low_price),
            "close": parse_float(close_price),
            "volume": parse_float(volume),
            "turnover_rate": None,
        }
    except (TypeError, ValueError):
        return None
    if has_turnover:
        bar["turnover_rate"] = coerce_optional_float(turnover_rate)
    return bar


def normalize_stock_code(raw: str) -> str:
    value = raw.strip().lower()
    if len(value) == 8 and value[:2] in {"sh", "sz", "bj"} and value[2:].isdigit():
        return value

    if len(value) == 6 and value.isdigit():
        first = value[0]
        if first in {"6", "9"}:
            return f"sh{value}"
        if first in {"0", "2", "3"}:
            return f"sz{value}"
        if first in {"4", "8"}:
            return f"bj{value}"

    raise ValueError("invalid stock code")


def to_numeric_code(stock_code: str) -> str:
    """Strip exchange prefix from ``sh600519`` / ``600519.SH`` style codes."""
    numeric_code = stock_code.split(".")[-1] if "." in stock_code else stock_code
    if numeric_code[:2] in {"sh", "sz", "bj"}:
        numeric_code = numeric_code[2:]
    return numeric_code


class MarketDataProvider(ABC):
    """Vendor I/O for daily bars, realtime quotes, and the A-share universe."""

    provider_id: str
    # EastMoney/Tencent silently truncate long qfq windows; baostock does not.
    chunks_long_qfq: bool = False

    def failover_targets(self) -> list[MarketDataProvider]:
        """Providers to try for one whole-range fetch (self unless this is a chain)."""
        return [self]

    @abstractmethod
    def fetch_daily_ohlcv(
        self,
        stock_code: str,
        start_date: date,
        end_date: date,
        *,
        adjust: str = QFQ,
    ) -> list[DailyBar]:
        """Inclusive ``[start_date, end_date]`` daily bars. Empty if no data."""

    @abstractmethod
    def fetch_realtime_quotes(
        self,
        stock_codes: list[str],
        *,
        timeout_seconds: float = 3.0,
    ) -> dict[str, RealtimeQuote]:
        """One-shot batch realtime quotes. Retry is the caller's job."""

    @abstractmethod
    def list_a_share_universe(self) -> list[StockMeta]:
        """A-share code/name table for the search cache."""
