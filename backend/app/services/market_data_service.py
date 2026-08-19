"""Market data service — provider-agnostic orchestration over daily bars and quotes."""

import logging
import time
from datetime import UTC, date, datetime, timedelta

from app.services.market_data import (
    QFQ,
    UNADJUSTED,
    MarketDataUnavailableError,
    MissingTradeDayBarError,
    StockDataFetchError,
    bar_date_str,
    get_market_data_provider,
)

logger = logging.getLogger(__name__)

# Long history is requested in ~1-year slices so EastMoney/Tencent do not
# silently truncate a 20-year window to the most recent ~1000 bars.
_QFQ_RANGE_CHUNK_DAYS = 365

__all__ = [
    "QFQ",
    "UNADJUSTED",
    "MarketDataUnavailableError",
    "MissingTradeDayBarError",
    "StockDataFetchError",
    "fetch_daily_bars",
    "fetch_qfq_bars_range",
    "fetch_realtime_quotes_batch",
    "fetch_trade_day_bar",
    "high_available_akshare",
]


def high_available_akshare(
    numeric_code: str,
    start_date: str,
    end_date: str,
    adjust: str = QFQ,
) -> list[dict]:
    """Thin wrapper around the provider for tests that still use YYYYMMDD strings."""
    start = date(int(start_date[:4]), int(start_date[4:6]), int(start_date[6:8]))
    end = date(int(end_date[:4]), int(end_date[4:6]), int(end_date[6:8]))
    return get_market_data_provider().fetch_daily_ohlcv(
        numeric_code, start, end, adjust=adjust
    )


def fetch_realtime_quotes_batch(
    stock_codes: list[str],
    timeout_seconds: float = 3.0,
    retries: int = 3,
    retry_backoff_seconds: float = 0.5,
) -> dict[str, dict]:
    """Fetch realtime quotes in batch with exponential backoff.

    Retries up to ``retries`` times after the first failure (user story 12).
    """
    if not stock_codes:
        return {}

    attempts = max(1, retries + 1)
    backoff = max(0.0, float(retry_backoff_seconds))
    last_exc: Exception | None = None
    for attempt in range(attempts):
        try:
            return get_market_data_provider().fetch_realtime_quotes(
                stock_codes, timeout_seconds=timeout_seconds
            )
        except ValueError:
            raise
        except (MarketDataUnavailableError, StockDataFetchError) as exc:
            last_exc = exc
            if attempt + 1 >= attempts:
                raise
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
    raise StockDataFetchError(f"Failed to fetch realtime quotes: {msg}") from last_exc


def fetch_daily_bars(
    stock_code: str, n: int, end_date: date | None = None
) -> list[dict]:
    """Fetch forward-adjusted (qfq) daily bars for the given stock.

    Returns list of dicts with keys: date, open, high, low, close, volume,
    turnover_rate (optional). The list is ordered oldest-first and does NOT
    include end_date itself.
    """
    if end_date is None:
        end_date = datetime.now(UTC).date()

    # We fetch extra days to account for non-trading days
    start_date = end_date - timedelta(days=n * 3 + 30)

    try:
        return get_market_data_provider().fetch_daily_ohlcv(
            stock_code,
            start_date,
            end_date - timedelta(days=1),
            adjust=QFQ,
        )
    except MarketDataUnavailableError:
        raise
    except Exception as exc:
        error_msg = str(exc)
        raise StockDataFetchError(
            f"Failed to fetch data for {stock_code}: {error_msg}"
        ) from exc


def _iter_date_chunks(
    start: date,
    end: date,
    chunk_days: int = _QFQ_RANGE_CHUNK_DAYS,
) -> list[tuple[date, date]]:
    """Split ``[start, end]`` into inclusive chunks of at most ``chunk_days``."""
    if start > end:
        return []
    chunks: list[tuple[date, date]] = []
    cursor = start
    step = timedelta(days=max(1, chunk_days))
    while cursor <= end:
        chunk_end = min(cursor + step - timedelta(days=1), end)
        chunks.append((cursor, chunk_end))
        cursor = chunk_end + timedelta(days=1)
    return chunks


def _merge_bars_by_date(chunks: list[list[dict]]) -> list[dict]:
    """Oldest-first union of bar lists; later chunks win on duplicate dates."""
    by_date: dict[str, dict] = {}
    for bars in chunks:
        for bar in bars:
            key = bar_date_str(bar.get("date"))
            if key:
                by_date[key] = bar
    return [by_date[key] for key in sorted(by_date)]


def _fetch_qfq_chunk(
    stock_code: str,
    start: date,
    end: date,
    *,
    retries: int,
    retry_backoff_seconds: float,
) -> list[dict]:
    """Fetch one date chunk with the same retry policy as the old full-range call."""
    attempts = max(1, retries + 1)
    last_exc: Exception | None = None
    for attempt in range(attempts):
        try:
            return get_market_data_provider().fetch_daily_ohlcv(
                stock_code, start, end, adjust=QFQ
            )
        except MarketDataUnavailableError as exc:
            last_exc = exc
            if attempt + 1 >= attempts:
                raise
            logger.warning(
                "fetch_qfq_bars_range retry %d/%d for %s %s..%s: %s",
                attempt + 1,
                attempts,
                stock_code,
                start.isoformat(),
                end.isoformat(),
                exc,
            )
            time.sleep(max(0.0, retry_backoff_seconds) * (attempt + 1))
        except Exception as exc:
            last_exc = exc
            if attempt + 1 >= attempts:
                raise StockDataFetchError(
                    f"Failed to fetch qfq bars range for {stock_code}: {exc}"
                ) from exc
            logger.warning(
                "fetch_qfq_bars_range retry %d/%d for %s %s..%s: %s",
                attempt + 1,
                attempts,
                stock_code,
                start.isoformat(),
                end.isoformat(),
                exc,
            )
            time.sleep(max(0.0, retry_backoff_seconds) * (attempt + 1))
    msg = str(last_exc) if last_exc else "unknown error"
    raise StockDataFetchError(
        f"Failed to fetch qfq bars range for {stock_code}: {msg}"
    ) from last_exc


def fetch_qfq_bars_range(
    stock_code: str,
    start_date: date,
    end_date: date,
    *,
    retries: int = 1,
    retry_backoff_seconds: float = 0.5,
) -> list[dict]:
    """Fetch forward-adjusted (qfq) daily bars for an explicit ``[start_date, end_date]``.

    Unlike :func:`fetch_daily_bars` (which derives its own lookback window from a
    day-count ``n``), this fetches a caller-specified date range. Used by the
    backtest worker to backfill gaps in ``daily_market_snapshots`` for the exact
    window a backtest job needs.

    Long ranges are requested in ~1-year chunks and merged oldest-first so a
    single-call API cap cannot silently drop the early history.

    Returns list of bar dicts (oldest-first) with keys: date, open, high, low,
    close, volume, turnover_rate (optional). Dates outside the stock's listed
    history are simply absent (no error).
    """
    if start_date > end_date:
        return []
    chunk_bars: list[list[dict]] = []
    for chunk_start, chunk_end in _iter_date_chunks(start_date, end_date):
        bars = _fetch_qfq_chunk(
            stock_code,
            chunk_start,
            chunk_end,
            retries=retries,
            retry_backoff_seconds=retry_backoff_seconds,
        )
        chunk_bars.append(bars)
    return _merge_bars_by_date(chunk_bars)


def fetch_trade_day_bar(
    stock_code: str,
    trade_date: date,
    *,
    retries: int = 1,
    retry_backoff_seconds: float = 0.5,
    adjust: str = QFQ,
) -> dict:
    """Fetch OHLCV (+ optional turnover) for a single trade date.

    Includes ``trade_date`` itself. Used by the post-close daily snapshot job.

    Defaults to forward-adjusted (``qfq``) prices so ``daily_market_snapshots``
    stays on the same basis used by baseline/backtest reads. For the most
    recent trading day qfq and unadjusted prices are identical. Pass
    ``adjust=""`` / ``"hfq"`` only if a caller explicitly needs another series.

    Returns:
        dict with keys: date, open, high, low, close, volume, turnover_rate.

    Raises:
        MissingTradeDayBarError: When the bar for ``trade_date`` is not present.
        MarketDataUnavailableError: When all data sources fail.
        StockDataFetchError: On other per-stock fetch failures.
    """
    day_str = trade_date.isoformat()
    attempts = max(1, retries + 1)
    last_exc: Exception | None = None

    for attempt in range(attempts):
        try:
            bars = get_market_data_provider().fetch_daily_ohlcv(
                stock_code, trade_date, trade_date, adjust=adjust
            )
            for bar in bars:
                if bar_date_str(bar.get("date")) == day_str:
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
            raise MissingTradeDayBarError(f"No daily bar for {stock_code} on {day_str}")
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
    raise StockDataFetchError(
        f"Failed to fetch trade-day bar for {stock_code}: {msg}"
    ) from last_exc
