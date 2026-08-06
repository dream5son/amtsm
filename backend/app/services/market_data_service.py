"""Market data service - fetches forward-adjusted daily bar data via akshare."""

import logging
import math
from datetime import UTC, date, datetime, timedelta

import akshare as ak
import httpx

logger = logging.getLogger(__name__)


class MarketDataUnavailableError(Exception):
    """Raised when the market data source is entirely unavailable."""


class StockDataFetchError(Exception):
    """Raised when fetching data for a specific stock fails."""


def fetch_realtime_quotes_batch(
    stock_codes: list[str],
    timeout_seconds: float = 3.0,
    retries: int = 1,
) -> dict[str, dict]:
    """Fetch realtime quotes from Sina in batch.

    Returns mapping:
      code -> {
        "stock_name": str,
        "price": float | None,
        "open": float | None,
        "prev_close": float | None,
        "quote_date": str | None,
        "quote_time": str | None,
        "is_halted": bool,
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

    last_exc: Exception | None = None
    for _ in range(max(1, retries + 1)):
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

    msg = str(last_exc) if last_exc else "unknown error"
    if "timed out" in msg.lower() or "connection" in msg.lower():
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

        name = fields[0].strip() if len(fields) >= 1 else code
        open_price = _to_float(fields[1]) if len(fields) >= 2 else None
        prev_close = _to_float(fields[2]) if len(fields) >= 3 else None
        price = _to_float(fields[3]) if len(fields) >= 4 else None
        quote_date = fields[30].strip() if len(fields) >= 31 and fields[30].strip() else None
        quote_time = fields[31].strip() if len(fields) >= 32 and fields[31].strip() else None

        is_halted = price is None or price <= 0

        result[code] = {
            "stock_name": name,
            "price": price,
            "open": open_price,
            "prev_close": prev_close,
            "quote_date": quote_date,
            "quote_time": quote_time,
            "is_halted": is_halted,
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
                "is_halted": True,
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

    Returns list of dicts with keys: date, open, high, low, close, volume.
    The list is ordered oldest-first and does NOT include end_date itself.
    """
    if end_date is None:
        end_date = datetime.now(UTC).date()

    # We fetch extra days to account for non-trading days
    start_date = end_date - timedelta(days=n * 3 + 30)

    try:
        # akshare uses 6-digit numeric code; strip exchange prefix if present
        numeric_code = stock_code.split(".")[-1] if "." in stock_code else stock_code

        df = ak.stock_zh_a_hist(
            symbol=numeric_code,
            period="daily",
            start_date=start_date.strftime("%Y%m%d"),
            end_date=(end_date - timedelta(days=1)).strftime("%Y%m%d"),
            adjust="qfq",
        )

        if df is None or df.empty:
            return []

        bars: list[dict] = []
        for _, row in df.iterrows():
            bars.append({
                "date": str(row["日期"]),
                "open": float(row["开盘"]),
                "high": float(row["最高"]),
                "low": float(row["最低"]),
                "close": float(row["收盘"]),
                "volume": float(row["成交量"]),
            })

        return bars

    except Exception as exc:
        error_msg = str(exc)
        if "timed out" in error_msg.lower() or "connection" in error_msg.lower():
            raise MarketDataUnavailableError(f"Market data source unavailable: {error_msg}") from exc
        raise StockDataFetchError(f"Failed to fetch data for {stock_code}: {error_msg}") from exc
