"""Market data service - fetches forward-adjusted daily bar data via akshare."""

import logging
import math
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from typing import Any
import time

import akshare as ak
import httpx
import pandas as pd

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# High-availability akshare source rotation
# ---------------------------------------------------------------------------

# Cooldown after a source fails: skip it for this many seconds before retrying.
_SOURCE_COOLDOWN_SECONDS = 300

# Tracks per-source failure time (source_name -> datetime when it failed).
# Reset to None when the source succeeds again.
_source_failure_time: dict[str, datetime] = {}


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
    # Default to sh for unknown prefixes
    return f"sh{numeric_code}"


def _bar_date_str(value: object) -> str:
    """Normalise a date-like value to ``YYYY-MM-DD``."""
    return str(value).strip()[:10]


def _optional_float(value: object) -> float | None:
    try:
        if value is None or (isinstance(value, float) and math.isnan(value)):
            return None
        if pd.isna(value):
            return None
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _normalize_df_em(df: pd.DataFrame) -> list[dict]:
    """Normalise a stock_zh_a_hist (EastMoney) DataFrame to bar dicts."""
    bars = []
    has_turnover = "换手率" in df.columns
    for _, row in df.iterrows():
        bars.append({
            "date": _bar_date_str(row["日期"]),
            "open": float(row["开盘"]),
            "high": float(row["最高"]),
            "low": float(row["最低"]),
            "close": float(row["收盘"]),
            "volume": float(row["成交量"]),
            "turnover_rate": _optional_float(row["换手率"]) if has_turnover else None,
        })
    return bars


def _normalize_df_tx(df: pd.DataFrame) -> list[dict]:
    """Normalise a stock_zh_a_hist_tx (Tencent) DataFrame to bar dicts."""
    bars = []
    col_map = {
        "date": ["date", "日期"],
        "open": ["open", "开盘"],
        "high": ["high", "最高"],
        "low": ["low", "最低"],
        "close": ["close", "收盘"],
        "volume": ["volume", "成交量"],
        "turnover_rate": ["turnover", "turnover_rate", "换手率"],
    }
    cols = {k: next((c for c in candidates if c in df.columns), None) for k, candidates in col_map.items()}
    for _, row in df.iterrows():
        turnover_col = cols["turnover_rate"]
        bars.append({
            "date": _bar_date_str(row[cols["date"]]),
            "open": float(row[cols["open"]]),
            "high": float(row[cols["high"]]),
            "low": float(row[cols["low"]]),
            "close": float(row[cols["close"]]),
            "volume": float(row[cols["volume"]]),
            "turnover_rate": _optional_float(row[turnover_col]) if turnover_col else None,
        })
    return bars


def _normalize_df_sina(df: pd.DataFrame) -> list[dict]:
    """Normalise a stock_zh_a_daily (Sina) DataFrame to bar dicts."""
    bars = []
    col_map = {
        "date": ["date", "日期"],
        "open": ["open", "开盘"],
        "high": ["high", "最高"],
        "low": ["low", "最低"],
        "close": ["close", "收盘"],
        "volume": ["volume", "成交量"],
        "turnover_rate": ["turnover", "turnover_rate", "换手率"],
    }
    cols = {k: next((c for c in candidates if c in df.columns), None) for k, candidates in col_map.items()}
    for _, row in df.iterrows():
        turnover_col = cols["turnover_rate"]
        bars.append({
            "date": _bar_date_str(row[cols["date"]]),
            "open": float(row[cols["open"]]),
            "high": float(row[cols["high"]]),
            "low": float(row[cols["low"]]),
            "close": float(row[cols["close"]]),
            "volume": float(row[cols["volume"]]),
            "turnover_rate": _optional_float(row[turnover_col]) if turnover_col else None,
        })
    return bars


def _to_numeric_code(stock_code: str) -> str:
    """Strip exchange prefix from ``sh600519`` / ``600519.SH`` style codes."""
    numeric_code = stock_code.split(".")[-1] if "." in stock_code else stock_code
    if numeric_code[:2] in {"sh", "sz", "bj"}:
        numeric_code = numeric_code[2:]
    return numeric_code


class MarketDataUnavailableError(Exception):
    """Raised when the market data source is entirely unavailable."""


class StockDataFetchError(Exception):
    """Raised when fetching data for a specific stock fails."""


class MissingTradeDayBarError(StockDataFetchError):
    """Raised when OHLCV for the requested trade date is absent."""


# Each entry: (source_name, akshare_fn, code_formatter, normaliser)
_AKSHARE_SOURCES: list[tuple[str, Callable[..., Any], Callable[[str], str], Callable[[pd.DataFrame], list[dict]]]] = [
    ("eastmoney", ak.stock_zh_a_hist, _code_em, _normalize_df_em),
    ("tencent", ak.stock_zh_a_hist_tx, _code_prefixed, _normalize_df_tx),
    ("sina", ak.stock_zh_a_daily, _code_prefixed, _normalize_df_sina),
]


def _is_source_available(source_name: str) -> bool:
    failure_time = _source_failure_time.get(source_name)
    if failure_time is None:
        return True
    elapsed = (datetime.now(UTC) - failure_time).total_seconds()
    return elapsed >= _SOURCE_COOLDOWN_SECONDS


def _mark_source_failed(source_name: str) -> None:
    _source_failure_time[source_name] = datetime.now(UTC)
    logger.warning("akshare source '%s' marked unavailable for %ds", source_name, _SOURCE_COOLDOWN_SECONDS)


def _mark_source_ok(source_name: str) -> None:
    if source_name in _source_failure_time:
        del _source_failure_time[source_name]
        logger.info("akshare source '%s' recovered and marked available", source_name)


def high_available_akshare(
    numeric_code: str,
    start_date: str,
    end_date: str,
    adjust: str = "qfq",
) -> list[dict]:
    """Fetch daily bars using akshare with automatic source rotation.

    Tries each configured source in order.  If a source fails (network error,
    empty response, etc.) it is marked unavailable for ``_SOURCE_COOLDOWN_SECONDS``
    seconds and the next source is tried immediately.  After the cooldown the
    source becomes eligible again.

    Args:
        numeric_code: 6-digit stock code without exchange prefix, e.g. ``"600519"``.
        start_date:   Start date in ``YYYYMMDD`` format.
        end_date:     End date in ``YYYYMMDD`` format (inclusive).
        adjust:       Adjustment type: ``"qfq"`` (forward), ``"hfq"`` (backward),
                      or ``""`` (unadjusted).  Defaults to ``"qfq"``.

    Returns:
        List of bar dicts with keys ``date``, ``open``, ``high``, ``low``,
        ``close``, ``volume``, ordered oldest-first.

    Raises:
        MarketDataUnavailableError: When *all* sources fail.
    """
    last_exc: Exception | None = None

    for source_name, fn, code_fmt, normalise in _AKSHARE_SOURCES:
        if not _is_source_available(source_name):
            logger.debug("akshare source '%s' is in cooldown, skipping", source_name)
            continue

        symbol = code_fmt(numeric_code)
        try:
            logger.debug("fetching %s from akshare source '%s'", numeric_code, source_name)
            df = fn(
                symbol=symbol,
                start_date=start_date,
                end_date=end_date,
                adjust=adjust,
            )
            if df is None or df.empty:
                raise StockDataFetchError(f"source '{source_name}' returned empty data for {numeric_code}")

            bars = normalise(df)
            _mark_source_ok(source_name)
            logger.debug(
                "akshare source '%s' returned %d bars for %s", source_name, len(bars), numeric_code
            )
            return bars

        except Exception as exc:
            error_msg = str(exc)
            logger.warning(
                "akshare source '%s' failed for %s: %s", source_name, numeric_code, error_msg
            )
            _mark_source_failed(source_name)
            last_exc = exc

    msg = str(last_exc) if last_exc else "all sources in cooldown"
    raise MarketDataUnavailableError(
        f"All akshare sources unavailable for {numeric_code}: {msg}"
    ) from last_exc


def fetch_realtime_quotes_batch(
    stock_codes: list[str],
    timeout_seconds: float = 3.0,
    retries: int = 3,
    retry_backoff_seconds: float = 0.5,
) -> dict[str, dict]:
    """Fetch realtime quotes from Sina in batch with exponential backoff.

    Retries up to ``retries`` times after the first failure (user story 12).

    Returns mapping:
      code -> {
        "stock_name": str,
        "price": float | None,
        "open": float | None,
        "prev_close": float | None,
        "quote_date": str | None,
        "quote_time": str | None,
        "is_halted": bool,
        "has_quote": bool,
      }
    """
    if not stock_codes:
        return {}

    invalid = [code for code in stock_codes if len(code) != 8 or code[:2] not in {"sh", "sz", "bj"}]
    if invalid:
        raise ValueError(f"invalid stock code(s): {', '.join(invalid)}")

    query_codes = ",".join(stock_codes)
    url = f"https://hq.sinajs.cn/list={query_codes}"
    headers = {"Referer": "https://finance.sina.com.cn"}

    attempts = max(1, retries + 1)
    backoff = max(0.0, float(retry_backoff_seconds))
    last_exc: Exception | None = None
    for attempt in range(attempts):
        try:
            response = httpx.get(
                url,
                timeout=timeout_seconds,
                headers=headers,
            )
            response.raise_for_status()
            return _parse_sina_realtime_quotes(response.text, stock_codes)
        except (httpx.HTTPError, ValueError) as exc:
            last_exc = exc
            if attempt + 1 >= attempts:
                break
            sleep_seconds = backoff * (2**attempt)
            logger.warning(
                "fetch_realtime_quotes_batch retry %d/%d codes=%s sleep=%.2fs error=%s",
                attempt + 1,
                retries,
                ",".join(stock_codes[:5]),
                sleep_seconds,
                exc,
            )
            if sleep_seconds > 0:
                time.sleep(sleep_seconds)

    msg = str(last_exc) if last_exc else "unknown error"
    if "timed out" in msg.lower() or "timeout" in msg.lower() or "connection" in msg.lower():
        raise MarketDataUnavailableError(f"Realtime market data unavailable: {msg}") from last_exc
    raise StockDataFetchError(f"Failed to fetch realtime quotes: {msg}") from last_exc


def _parse_sina_realtime_quotes(raw_text: str, stock_codes: list[str]) -> dict[str, dict]:
    result: dict[str, dict] = {}

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

        # Empty payload means the code was acknowledged but has no usable fields.
        has_quote = bool(payload) and len(fields) >= 4
        name = fields[0].strip() if len(fields) >= 1 and fields[0].strip() else code
        open_price = _to_float(fields[1]) if len(fields) >= 2 else None
        prev_close = _to_float(fields[2]) if len(fields) >= 3 else None
        price = _to_float(fields[3]) if len(fields) >= 4 else None
        quote_date = fields[30].strip() if len(fields) >= 31 and fields[30].strip() else None
        quote_time = fields[31].strip() if len(fields) >= 32 and fields[31].strip() else None

        is_halted = has_quote and (price is None or price <= 0)

        result[code] = {
            "stock_name": name,
            "price": price,
            "open": open_price,
            "prev_close": prev_close,
            "quote_date": quote_date,
            "quote_time": quote_time,
            "is_halted": is_halted,
            "has_quote": has_quote,
        }

    for code in stock_codes:
        result.setdefault(
            code,
            {
                "stock_name": code,
                "price": None,
                "open": None,
                "prev_close": None,
                "quote_date": None,
                "quote_time": None,
                "is_halted": False,
                "has_quote": False,
            },
        )

    return result


def _to_float(value: str) -> float | None:
    try:
        number = float(value)
        if math.isnan(number):
            return None
        return number
    except (TypeError, ValueError):
        return None


def fetch_daily_bars(stock_code: str, n: int, end_date: date | None = None) -> list[dict]:
    """Fetch forward-adjusted (qfq) daily bars for the given stock.

    Uses :func:`high_available_akshare` to try multiple akshare data sources
    with automatic failover.

    Returns list of dicts with keys: date, open, high, low, close, volume,
    turnover_rate (optional). The list is ordered oldest-first and does NOT
    include end_date itself.
    """
    if end_date is None:
        end_date = datetime.now(UTC).date()

    # We fetch extra days to account for non-trading days
    start_date = end_date - timedelta(days=n * 3 + 30)
    numeric_code = _to_numeric_code(stock_code)

    try:
        return high_available_akshare(
            numeric_code=numeric_code,
            start_date=start_date.strftime("%Y%m%d"),
            end_date=(end_date - timedelta(days=1)).strftime("%Y%m%d"),
            adjust="qfq",
        )
    except MarketDataUnavailableError:
        raise
    except Exception as exc:
        error_msg = str(exc)
        raise StockDataFetchError(f"Failed to fetch data for {stock_code}: {error_msg}") from exc


def fetch_trade_day_bar(
    stock_code: str,
    trade_date: date,
    *,
    retries: int = 1,
    retry_backoff_seconds: float = 0.5,
) -> dict:
    """Fetch unadjusted OHLCV (+ optional turnover) for a single trade date.

    Includes ``trade_date`` itself. Used by the post-close daily snapshot job.

    Returns:
        dict with keys: date, open, high, low, close, volume, turnover_rate.

    Raises:
        MissingTradeDayBarError: When the bar for ``trade_date`` is not present.
        MarketDataUnavailableError: When all data sources fail.
        StockDataFetchError: On other per-stock fetch failures.
    """
    numeric_code = _to_numeric_code(stock_code)
    day_str = trade_date.isoformat()
    yyyymmdd = trade_date.strftime("%Y%m%d")
    attempts = max(1, retries + 1)
    last_exc: Exception | None = None

    for attempt in range(attempts):
        try:
            # Unadjusted prices reflect the actual session OHLCV for archival.
            bars = high_available_akshare(
                numeric_code=numeric_code,
                start_date=yyyymmdd,
                end_date=yyyymmdd,
                adjust="",
            )
            for bar in bars:
                if _bar_date_str(bar.get("date")) == day_str:
                    open_price = bar.get("open")
                    high_price = bar.get("high")
                    low_price = bar.get("low")
                    close_price = bar.get("close")
                    volume = bar.get("volume")
                    if None in (open_price, high_price, low_price, close_price, volume):
                        raise MissingTradeDayBarError(
                            f"Incomplete OHLCV for {stock_code} on {day_str}"
                        )
                    return {
                        "date": day_str,
                        "open": float(open_price),
                        "high": float(high_price),
                        "low": float(low_price),
                        "close": float(close_price),
                        "volume": float(volume),
                        "turnover_rate": bar.get("turnover_rate"),
                    }
            raise MissingTradeDayBarError(
                f"No daily bar for {stock_code} on {day_str}"
            )
        except MissingTradeDayBarError:
            raise
        except MarketDataUnavailableError as exc:
            last_exc = exc
            if attempt + 1 >= attempts:
                raise
            logger.warning(
                "fetch_trade_day_bar retry %d/%d for %s: %s",
                attempt + 1,
                attempts,
                stock_code,
                exc,
            )
            time.sleep(max(0.0, retry_backoff_seconds) * (attempt + 1))
        except Exception as exc:
            last_exc = exc
            if attempt + 1 >= attempts:
                raise StockDataFetchError(
                    f"Failed to fetch trade-day bar for {stock_code}: {exc}"
                ) from exc
            logger.warning(
                "fetch_trade_day_bar retry %d/%d for %s: %s",
                attempt + 1,
                attempts,
                stock_code,
                exc,
            )
            time.sleep(max(0.0, retry_backoff_seconds) * (attempt + 1))

    msg = str(last_exc) if last_exc else "unknown error"
    raise StockDataFetchError(f"Failed to fetch trade-day bar for {stock_code}: {msg}") from last_exc
