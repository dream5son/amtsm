"""Baostock-backed market data provider (daily K + A-share universe)."""

from __future__ import annotations

import io
import logging
from contextlib import redirect_stderr, redirect_stdout
from datetime import date
from threading import Lock
from typing import Any, Protocol

from pypinyin import Style, lazy_pinyin

from app.services.market_data.base import (
    QFQ,
    UNADJUSTED,
    DailyBar,
    MarketDataProvider,
    MarketDataUnavailableError,
    RealtimeQuote,
    StockMeta,
    build_daily_bar,
    normalize_stock_code,
)

logger = logging.getLogger(__name__)

_DAILY_FIELDS = "date,open,high,low,close,volume,turn"
_STOCK_TYPE = "1"
_LISTED_STATUS = "1"

_ADJUSTFLAG = {
    QFQ: "2",
    UNADJUSTED: "3",
    "hfq": "1",
    "1": "1",
    "2": "2",
    "3": "3",
}


class _LoginResult(Protocol):
    error_code: str
    error_msg: str


class _QueryResult(Protocol):
    error_code: str
    error_msg: str
    fields: list[str]

    def next(self) -> bool: ...

    def get_row_data(self) -> list[str]: ...


class _BaostockClient(Protocol):
    def login(self) -> _LoginResult: ...

    def query_history_k_data_plus(
        self,
        code: str,
        fields: str,
        start_date: str | None = None,
        end_date: str | None = None,
        frequency: str = "d",
        adjustflag: str = "3",
    ) -> _QueryResult: ...

    def query_stock_basic(
        self,
        code: str | None = None,
        code_name: str | None = None,
    ) -> _QueryResult: ...


def _exchange_from_normalized(code: str) -> str:
    prefix = code[:2]
    if prefix == "sh":
        return "SH"
    if prefix == "sz":
        return "SZ"
    if prefix == "bj":
        return "BJ"
    return "UNKNOWN"


def _get_initials(name: str) -> str:
    letters = lazy_pinyin(name, style=Style.FIRST_LETTER, strict=False)
    return "".join(letters).upper()


def to_baostock_code(stock_code: str) -> str:
    """Convert ``sh600519`` / ``600519`` / ``sh.600519`` to ``sh.600519``."""
    value = normalize_stock_code(stock_code.strip().lower().replace(".", ""))
    return f"{value[:2]}.{value[2:]}"


def from_baostock_code(code: str) -> str:
    """Convert ``sh.600519`` to internal ``sh600519``."""
    return normalize_stock_code(code.strip().lower().replace(".", ""))


def _adjustflag(adjust: str) -> str:
    try:
        return _ADJUSTFLAG[adjust]
    except KeyError as exc:
        raise ValueError(f"unsupported adjust flag: {adjust!r}") from exc


def _collect_rows(result: _QueryResult) -> list[dict[str, str]]:
    if result.error_code != "0":
        raise MarketDataUnavailableError(
            f"baostock query failed: {result.error_code} {result.error_msg}"
        )
    rows: list[dict[str, str]] = []
    fields = list(result.fields)
    while result.next():
        values = result.get_row_data()
        rows.append(dict(zip(fields, values, strict=False)))
    return rows


def _normalize_daily_rows(rows: list[dict[str, str]]) -> list[DailyBar]:
    bars: list[DailyBar] = []
    for row in rows:
        bar = build_daily_bar(
            date=row.get("date"),
            open_price=row.get("open"),
            high_price=row.get("high"),
            low_price=row.get("low"),
            close_price=row.get("close"),
            volume=row.get("volume"),
            turnover_rate=row.get("turn"),
            has_turnover=True,
        )
        if bar is not None:
            bars.append(bar)
    return bars


class BaostockMarketDataProvider(MarketDataProvider):
    """Daily bars and A-share universe via baostock. Realtime is not provided."""

    provider_id = "baostock"

    def __init__(self, client: _BaostockClient | None = None) -> None:
        self._client = client
        self._lock = Lock()
        self._logged_in = False

    def _bs(self) -> _BaostockClient:
        if self._client is None:
            import baostock as bs

            self._client = bs
        return self._client

    def _ensure_login(self) -> None:
        if self._logged_in:
            return
        sink = io.StringIO()
        try:
            with redirect_stdout(sink), redirect_stderr(sink):
                result = self._bs().login()
        except Exception as exc:
            raise MarketDataUnavailableError(f"baostock login failed: {exc}") from exc
        if result.error_code != "0":
            raise MarketDataUnavailableError(
                f"baostock login failed: {result.error_code} {result.error_msg}"
            )
        self._logged_in = True

    def _query(self, method_name: str, *args: Any, **kwargs: Any) -> _QueryResult:
        with self._lock:
            self._ensure_login()
            method = getattr(self._bs(), method_name)
            result = method(*args, **kwargs)
            if result.error_code != "0":
                self._logged_in = False
                self._ensure_login()
                result = method(*args, **kwargs)
            return result

    def fetch_daily_ohlcv(
        self,
        stock_code: str,
        start_date: date,
        end_date: date,
        *,
        adjust: str = QFQ,
    ) -> list[DailyBar]:
        if start_date > end_date:
            return []
        code = to_baostock_code(stock_code)
        try:
            result = self._query(
                "query_history_k_data_plus",
                code,
                _DAILY_FIELDS,
                start_date=start_date.isoformat(),
                end_date=end_date.isoformat(),
                frequency="d",
                adjustflag=_adjustflag(adjust),
            )
            return _normalize_daily_rows(_collect_rows(result))
        except MarketDataUnavailableError:
            raise
        except ValueError:
            raise
        except Exception as exc:
            raise MarketDataUnavailableError(
                f"baostock daily bars unavailable for {code}: {exc}"
            ) from exc

    def fetch_realtime_quotes(
        self,
        stock_codes: list[str],
        *,
        timeout_seconds: float = 3.0,
    ) -> dict[str, RealtimeQuote]:
        raise NotImplementedError("baostock does not provide realtime quotes")

    def list_a_share_universe(self) -> list[StockMeta]:
        try:
            result = self._query("query_stock_basic")
            rows = _collect_rows(result)
        except MarketDataUnavailableError:
            raise
        except Exception as exc:
            raise MarketDataUnavailableError(
                f"baostock stock universe unavailable: {exc}"
            ) from exc

        items: list[StockMeta] = []
        for row in rows:
            if str(row.get("type", "")).strip() != _STOCK_TYPE:
                continue
            if str(row.get("status", "")).strip() != _LISTED_STATUS:
                continue
            raw_code = str(row.get("code", "")).strip()
            name = str(row.get("code_name", "")).strip()
            if not raw_code or not name:
                continue
            try:
                normalized = from_baostock_code(raw_code)
            except ValueError:
                continue
            items.append(
                StockMeta(
                    stock_code=normalized,
                    stock_name=name,
                    exchange=_exchange_from_normalized(normalized),
                    short_code=normalized[2:],
                    initials=_get_initials(name),
                )
            )
        return items
