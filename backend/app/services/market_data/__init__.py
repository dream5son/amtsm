"""Market data providers. Default is baostock with akshare failover."""

from app.services.market_data.akshare_provider import AkshareMarketDataProvider
from app.services.market_data.baostock_provider import BaostockMarketDataProvider
from app.services.market_data.base import (
    QFQ,
    UNADJUSTED,
    DailyBar,
    MarketDataProvider,
    MarketDataUnavailableError,
    MissingTradeDayBarError,
    RealtimeQuote,
    StockDataFetchError,
    StockMeta,
    bar_date_str,
    normalize_stock_code,
    to_numeric_code,
)
from app.services.market_data.failover import FailoverMarketDataProvider

_provider: MarketDataProvider | None = None


def get_market_data_provider() -> MarketDataProvider:
    """Return the process-local provider: baostock first, then akshare."""
    global _provider
    if _provider is None:
        _provider = FailoverMarketDataProvider(
            [BaostockMarketDataProvider(), AkshareMarketDataProvider()]
        )
    return _provider


__all__ = [
    "QFQ",
    "UNADJUSTED",
    "AkshareMarketDataProvider",
    "BaostockMarketDataProvider",
    "DailyBar",
    "FailoverMarketDataProvider",
    "MarketDataProvider",
    "MarketDataUnavailableError",
    "MissingTradeDayBarError",
    "RealtimeQuote",
    "StockDataFetchError",
    "StockMeta",
    "bar_date_str",
    "get_market_data_provider",
    "normalize_stock_code",
    "to_numeric_code",
]
