"""Market data service - fetches forward-adjusted daily bar data via akshare."""

import logging
from datetime import date, timedelta

import akshare as ak

logger = logging.getLogger(__name__)


class MarketDataUnavailableError(Exception):
    """Raised when the market data source is entirely unavailable."""


class StockDataFetchError(Exception):
    """Raised when fetching data for a specific stock fails."""


def fetch_daily_bars(stock_code: str, n: int, end_date: date | None = None) -> list[dict]:
    """Fetch forward-adjusted (qfq) daily bars for the given stock.

    Returns list of dicts with keys: date, open, high, low, close, volume.
    The list is ordered oldest-first and does NOT include end_date itself.
    """
    if end_date is None:
        end_date = date.today()

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
