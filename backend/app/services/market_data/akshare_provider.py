"""Akshare-backed market data provider (daily three-source rotation + Sina quotes)."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, date, datetime
from threading import Lock
from typing import Any

import akshare as ak
import httpx
import pandas as pd
from pypinyin import Style, lazy_pinyin

from app.services.market_data.base import (
    QFQ,
    DailyBar,
    MarketDataProvider,
    MarketDataUnavailableError,
    RealtimeQuote,
    StockDataFetchError,
    StockMeta,
    build_daily_bar,
    normalize_stock_code,
    to_numeric_code,
)
from app.services.market_data.numbers import coerce_optional_float

logger = logging.getLogger(__name__)

_SOURCE_COOLDOWN_SECONDS = 300

# stock_zh_a_daily constructs MiniRacer(); concurrent V8 isolate init can
# SIGABRT the process. Serial use is safe across provider instances.
_sina_lock = Lock()


def _code_em(numeric_code: str) -> str:
    """Return bare 6-digit code expected by stock_zh_a_hist (EastMoney)."""
    return numeric_code


def _code_prefixed(numeric_code: str) -> str:
    """Return sh/sz/bj-prefixed code expected by Sina / Tencent sources."""
    if numeric_code.startswith(("60", "68", "90")):
        return f"sh{numeric_code}"
    if numeric_code.startswith(("00", "30", "20")):
        return f"sz{numeric_code}"
    if numeric_code.startswith(("43", "83", "87", "88")):
        return f"bj{numeric_code}"
    return f"sh{numeric_code}"


def _normalize_df_em(df: pd.DataFrame) -> list[DailyBar]:
    """Normalise a stock_zh_a_hist (EastMoney) DataFrame to bar dicts."""
    bars: list[DailyBar] = []
    has_turnover = "换手率" in df.columns
    for _, row in df.iterrows():
        bar = build_daily_bar(
            date=row["日期"],
            open_price=row["开盘"],
            high_price=row["最高"],
            low_price=row["最低"],
            close_price=row["收盘"],
            volume=row["成交量"],
            turnover_rate=row["换手率"] if has_turnover else None,
            has_turnover=has_turnover,
        )
        if bar is not None:
            bars.append(bar)
    return bars


def _normalize_df_tx(df: pd.DataFrame) -> list[DailyBar]:
    """Normalise a stock_zh_a_hist_tx (Tencent) DataFrame to bar dicts."""
    bars: list[DailyBar] = []
    col_map = {
        "date": ["date", "日期"],
        "open": ["open", "开盘"],
        "high": ["high", "最高"],
        "low": ["low", "最低"],
        "close": ["close", "收盘"],
        "volume": ["volume", "成交量"],
        "turnover_rate": ["turnover", "turnover_rate", "换手率"],
    }
    cols = {
        k: next((c for c in candidates if c in df.columns), None)
        for k, candidates in col_map.items()
    }
    required = ("date", "open", "high", "low", "close", "volume")
    if any(cols[key] is None for key in required):
        return []
    for _, row in df.iterrows():
        turnover_col = cols["turnover_rate"]
        bar = build_daily_bar(
            date=row[cols["date"]],
            open_price=row[cols["open"]],
            high_price=row[cols["high"]],
            low_price=row[cols["low"]],
            close_price=row[cols["close"]],
            volume=row[cols["volume"]],
            turnover_rate=row[turnover_col] if turnover_col else None,
            has_turnover=turnover_col is not None,
        )
        if bar is not None:
            bars.append(bar)
    return bars


def _normalize_df_sina(df: pd.DataFrame) -> list[DailyBar]:
    """Normalise a stock_zh_a_daily (Sina) DataFrame to bar dicts."""
    bars: list[DailyBar] = []
    col_map = {
        "date": ["date", "日期"],
        "open": ["open", "开盘"],
        "high": ["high", "最高"],
        "low": ["low", "最低"],
        "close": ["close", "收盘"],
        "volume": ["volume", "成交量"],
        "turnover_rate": ["turnover", "turnover_rate", "换手率"],
    }
    cols = {
        k: next((c for c in candidates if c in df.columns), None)
        for k, candidates in col_map.items()
    }
    required = ("date", "open", "high", "low", "close", "volume")
    if any(cols[key] is None for key in required):
        return []
    for _, row in df.iterrows():
        turnover_col = cols["turnover_rate"]
        bar = build_daily_bar(
            date=row[cols["date"]],
            open_price=row[cols["open"]],
            high_price=row[cols["high"]],
            low_price=row[cols["low"]],
            close_price=row[cols["close"]],
            volume=row[cols["volume"]],
            turnover_rate=row[turnover_col] if turnover_col else None,
            has_turnover=turnover_col is not None,
        )
        if bar is not None:
            bars.append(bar)
    return bars


AkshareSource = tuple[
    str,
    Callable[..., Any],
    Callable[[str], str],
    Callable[[pd.DataFrame], list[DailyBar]],
]

_AKSHARE_SOURCES: list[AkshareSource] = [
    ("eastmoney", ak.stock_zh_a_hist, _code_em, _normalize_df_em),
    ("tencent", ak.stock_zh_a_hist_tx, _code_prefixed, _normalize_df_tx),
    ("sina", ak.stock_zh_a_daily, _code_prefixed, _normalize_df_sina),
]


def _empty_quote(code: str) -> RealtimeQuote:
    return {
        "stock_name": code,
        "price": None,
        "open": None,
        "prev_close": None,
        "high": None,
        "low": None,
        "volume": None,
        "quote_date": None,
        "quote_time": None,
        "is_halted": False,
        "has_quote": False,
    }


def _parse_sina_realtime_quotes(
    raw_text: str, stock_codes: list[str]
) -> dict[str, RealtimeQuote]:
    result: dict[str, RealtimeQuote] = {}

    for line in raw_text.splitlines():
        text = line.strip()
        if not text or "hq_str_" not in text or "=" not in text:
            continue

        left, right = text.split("=", 1)
        code = left.split("hq_str_")[-1]
        payload = right.strip().strip(";").strip('"')
        fields = payload.split(",") if payload else []
        if code not in stock_codes:
            continue

        has_quote = bool(payload) and len(fields) >= 4
        name = fields[0].strip() if len(fields) >= 1 and fields[0].strip() else code
        open_price = coerce_optional_float(fields[1]) if len(fields) >= 2 else None
        prev_close = coerce_optional_float(fields[2]) if len(fields) >= 3 else None
        price = coerce_optional_float(fields[3]) if len(fields) >= 4 else None
        high_price = coerce_optional_float(fields[4]) if len(fields) >= 5 else None
        low_price = coerce_optional_float(fields[5]) if len(fields) >= 6 else None
        volume = coerce_optional_float(fields[8]) if len(fields) >= 9 else None
        quote_date = (
            fields[30].strip() if len(fields) >= 31 and fields[30].strip() else None
        )
        quote_time = (
            fields[31].strip() if len(fields) >= 32 and fields[31].strip() else None
        )

        # Halt is a parsed non-positive price, not a missing/unparseable field.
        is_halted = has_quote and price is not None and price <= 0

        result[code] = {
            "stock_name": name,
            "price": price,
            "open": open_price,
            "prev_close": prev_close,
            "high": high_price,
            "low": low_price,
            "volume": volume,
            "quote_date": quote_date,
            "quote_time": quote_time,
            "is_halted": is_halted,
            "has_quote": has_quote,
        }

    for code in stock_codes:
        result.setdefault(code, _empty_quote(code))

    return result


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


class AkshareMarketDataProvider(MarketDataProvider):
    """Daily bars via akshare failover; realtime quotes via Sina HTTP."""

    provider_id = "akshare"

    def __init__(self, sources: list[AkshareSource] | None = None) -> None:
        self._sources = sources if sources is not None else list(_AKSHARE_SOURCES)
        self._source_failure_time: dict[str, datetime] = {}
        self._source_lock = Lock()

    def _is_source_available(self, source_name: str) -> bool:
        failure_time = self._source_failure_time.get(source_name)
        if failure_time is None:
            return True
        elapsed = (datetime.now(UTC) - failure_time).total_seconds()
        return elapsed >= _SOURCE_COOLDOWN_SECONDS

    def _mark_source_failed(self, source_name: str) -> None:
        self._source_failure_time[source_name] = datetime.now(UTC)
        logger.warning(
            "akshare source '%s' marked unavailable for %ds",
            source_name,
            _SOURCE_COOLDOWN_SECONDS,
        )

    def _mark_source_ok(self, source_name: str) -> None:
        if source_name in self._source_failure_time:
            del self._source_failure_time[source_name]
            logger.info(
                "akshare source '%s' recovered and marked available", source_name
            )

    def _call_akshare_source(
        self,
        source_name: str,
        fn: Callable[..., Any],
        *,
        symbol: str,
        start_date: str,
        end_date: str,
        adjust: str,
    ) -> Any:
        kwargs = {
            "symbol": symbol,
            "start_date": start_date,
            "end_date": end_date,
            "adjust": adjust,
        }
        if source_name == "sina":
            with _sina_lock:
                return fn(**kwargs)
        return fn(**kwargs)

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
        numeric_code = to_numeric_code(stock_code)
        return self._fetch_with_source_rotation(
            numeric_code,
            start_date.strftime("%Y%m%d"),
            end_date.strftime("%Y%m%d"),
            adjust=adjust,
        )

    def _fetch_with_source_rotation(
        self,
        numeric_code: str,
        start_date: str,
        end_date: str,
        adjust: str = QFQ,
    ) -> list[DailyBar]:
        last_exc: Exception | None = None

        for source_name, fn, code_fmt, normalise in self._sources:
            with self._source_lock:
                available = self._is_source_available(source_name)
            if not available:
                logger.debug(
                    "akshare source '%s' is in cooldown, skipping", source_name
                )
                continue

            symbol = code_fmt(numeric_code)
            try:
                logger.debug(
                    "fetching %s from akshare source '%s'", numeric_code, source_name
                )
                df = self._call_akshare_source(
                    source_name,
                    fn,
                    symbol=symbol,
                    start_date=start_date,
                    end_date=end_date,
                    adjust=adjust,
                )
                if df is None or df.empty:
                    raise StockDataFetchError(
                        f"source '{source_name}' returned empty data for {numeric_code}"
                    )

                bars = normalise(df)
                if not bars:
                    raise StockDataFetchError(
                        f"source '{source_name}' returned unusable data for {numeric_code}"
                    )
                with self._source_lock:
                    self._mark_source_ok(source_name)
                logger.debug(
                    "akshare source '%s' returned %d bars for %s",
                    source_name,
                    len(bars),
                    numeric_code,
                )
                return bars

            except Exception as exc:  # noqa: BLE001 - failover must catch any source failure
                logger.warning(
                    "akshare source '%s' failed for %s: %s",
                    source_name,
                    numeric_code,
                    exc,
                )
                with self._source_lock:
                    self._mark_source_failed(source_name)
                last_exc = exc

        msg = str(last_exc) if last_exc else "all sources in cooldown"
        raise MarketDataUnavailableError(
            f"All akshare sources unavailable for {numeric_code}: {msg}"
        ) from last_exc

    def fetch_realtime_quotes(
        self,
        stock_codes: list[str],
        *,
        timeout_seconds: float = 3.0,
    ) -> dict[str, RealtimeQuote]:
        if not stock_codes:
            return {}

        invalid = [
            code
            for code in stock_codes
            if len(code) != 8 or code[:2] not in {"sh", "sz", "bj"}
        ]
        if invalid:
            raise ValueError(f"invalid stock code(s): {', '.join(invalid)}")

        query_codes = ",".join(stock_codes)
        url = f"https://hq.sinajs.cn/list={query_codes}"
        headers = {"Referer": "https://finance.sina.com.cn"}
        try:
            response = httpx.get(url, timeout=timeout_seconds, headers=headers)
            response.raise_for_status()
            return _parse_sina_realtime_quotes(response.text, stock_codes)
        except httpx.TransportError as exc:
            raise MarketDataUnavailableError(
                f"Realtime market data unavailable: {exc}"
            ) from exc
        except httpx.HTTPError as exc:
            raise StockDataFetchError(
                f"Failed to fetch realtime quotes: {exc}"
            ) from exc

    def list_a_share_universe(self) -> list[StockMeta]:
        df = ak.stock_info_a_code_name()
        items: list[StockMeta] = []

        for row in df.itertuples(index=False):
            code = str(getattr(row, "code", "")).strip()
            name = str(getattr(row, "name", "")).strip()
            if not code or not name:
                continue

            try:
                normalized = normalize_stock_code(code)
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
