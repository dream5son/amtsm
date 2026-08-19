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

# EastMoney/Tencent silently truncate a 20-year window to the most recent
# ~1000 bars. Only those providers (``chunks_long_qfq``) are sliced; baostock
# 涨跌幅复权 is consistent across any range and must be fetched in one shot.
_QFQ_RANGE_CHUNK_DAYS = 365
_QFQ_CHUNK_OVERLAP_DAYS = 14
_PRICE_KEYS = ("open", "high", "low", "close")

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
    overlap_days: int = 0,
) -> list[tuple[date, date]]:
    """Split ``[start, end]`` into inclusive chunks of at most ``chunk_days``.

    When ``overlap_days`` > 0, adjacent chunks share that many calendar days so
    EastMoney-style qfq (relative to each chunk's last bar) can be rescaled
    onto a common basis.
    """
    if start > end:
        return []
    chunks: list[tuple[date, date]] = []
    cursor = start
    step = timedelta(days=max(1, chunk_days))
    overlap = timedelta(days=max(0, overlap_days))
    while cursor <= end:
        chunk_end = min(cursor + step - timedelta(days=1), end)
        chunks.append((cursor, chunk_end))
        if chunk_end >= end:
            break
        next_start = chunk_end + timedelta(days=1) - overlap
        if next_start <= cursor:
            next_start = chunk_end + timedelta(days=1)
        cursor = next_start
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


def _scale_ohlc(bar: dict, ratio: float) -> dict:
    """Return a copy of ``bar`` with OHLC multiplied by ``ratio`` (volume unchanged)."""
    scaled = dict(bar)
    for key in _PRICE_KEYS:
        value = scaled.get(key)
        if value is not None:
            scaled[key] = float(value) * ratio
    return scaled


def _overlap_scale_ratio(older: list[dict], newer: list[dict]) -> float:
    """Median ``newer.close / older.close`` on overlapping dates (1.0 if none)."""
    newer_by_date = {bar_date_str(bar.get("date")): bar for bar in newer}
    ratios: list[float] = []
    for bar in older:
        key = bar_date_str(bar.get("date"))
        other = newer_by_date.get(key)
        if other is None:
            continue
        old_close = float(bar.get("close") or 0.0)
        new_close = float(other.get("close") or 0.0)
        if old_close > 0 and new_close > 0:
            ratios.append(new_close / old_close)
    if not ratios:
        return 1.0
    ratios.sort()
    return ratios[len(ratios) // 2]


def _merge_qfq_chunks_rescaled(chunks: list[list[dict]]) -> list[dict]:
    """Stitch qfq chunks onto the newest chunk's price basis via overlap ratios."""
    non_empty = [bars for bars in chunks if bars]
    if not non_empty:
        return []
    merged = list(non_empty[-1])
    for older in reversed(non_empty[:-1]):
        ratio = _overlap_scale_ratio(older, merged)
        scaled = (
            older
            if abs(ratio - 1.0) <= 1e-12
            else [_scale_ohlc(bar, ratio) for bar in older]
        )
        merged = _merge_bars_by_date([scaled, merged])
    return merged


def _fetch_qfq_once(
    provider,
    stock_code: str,
    start: date,
    end: date,
    *,
    retries: int,
    retry_backoff_seconds: float,
) -> list[dict]:
    """Fetch one inclusive range from a pinned provider, with retries."""
    attempts = max(1, retries + 1)
    last_exc: Exception | None = None
    for attempt in range(attempts):
        try:
            return provider.fetch_daily_ohlcv(stock_code, start, end, adjust=QFQ)
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


def _fetch_qfq_chunked(
    provider,
    stock_code: str,
    start_date: date,
    end_date: date,
    *,
    retries: int,
    retry_backoff_seconds: float,
) -> list[dict]:
    """Year-chunk a truncating qfq source and rescale onto the newest basis."""
    chunk_bars: list[list[dict]] = []
    seen_bars = False
    today = datetime.now(UTC).date()
    for chunk_start, chunk_end in _iter_date_chunks(
        start_date,
        end_date,
        overlap_days=_QFQ_CHUNK_OVERLAP_DAYS,
    ):
        try:
            bars = _fetch_qfq_once(
                provider,
                stock_code,
                chunk_start,
                chunk_end,
                retries=retries,
                retry_backoff_seconds=retry_backoff_seconds,
            )
        except (MarketDataUnavailableError, StockDataFetchError):
            if seen_bars and chunk_end < today - timedelta(days=7):
                raise
            bars = []
        if bars:
            seen_bars = True
            chunk_bars.append(bars)
            continue
        if seen_bars and chunk_end < today - timedelta(days=7):
            raise MarketDataUnavailableError(
                f"empty qfq chunk after listed history for {stock_code} "
                f"{chunk_start.isoformat()}..{chunk_end.isoformat()}"
            )
        chunk_bars.append([])
    return _merge_qfq_chunks_rescaled(chunk_bars)


def _fetch_qfq_from_provider(
    provider,
    stock_code: str,
    start_date: date,
    end_date: date,
    *,
    retries: int,
    retry_backoff_seconds: float,
) -> list[dict]:
    if getattr(provider, "chunks_long_qfq", False):
        return _fetch_qfq_chunked(
            provider,
            stock_code,
            start_date,
            end_date,
            retries=retries,
            retry_backoff_seconds=retry_backoff_seconds,
        )
    return _fetch_qfq_once(
        provider,
        stock_code,
        start_date,
        end_date,
        retries=retries,
        retry_backoff_seconds=retry_backoff_seconds,
    )


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

    Baostock is queried as a single ``[start, end]`` call (涨跌幅复权 is already
    on today's basis). Truncating sources (EastMoney/Tencent via akshare) are
    requested in overlapping ~1-year chunks, rescaled onto the newest chunk,
    and merged oldest-first. The whole range pins one provider so chunks never
    mix qfq algorithms.

    Returns list of bar dicts (oldest-first) with keys: date, open, high, low,
    close, volume, turnover_rate (optional). Dates outside the stock's listed
    history are simply absent (no error).
    """
    if start_date > end_date:
        return []
    root = get_market_data_provider()
    targets = list(getattr(root, "failover_targets", lambda: [root])())
    last_exc: Exception | None = None
    for provider in targets:
        try:
            return _fetch_qfq_from_provider(
                provider,
                stock_code,
                start_date,
                end_date,
                retries=retries,
                retry_backoff_seconds=retry_backoff_seconds,
            )
        except (MarketDataUnavailableError, StockDataFetchError) as exc:
            last_exc = exc
            logger.warning(
                "fetch_qfq_bars_range provider '%s' failed for %s: %s",
                getattr(provider, "provider_id", "?"),
                stock_code,
                exc,
            )
    if isinstance(last_exc, MarketDataUnavailableError):
        raise last_exc
    msg = str(last_exc) if last_exc else "unknown error"
    raise StockDataFetchError(
        f"Failed to fetch qfq bars range for {stock_code}: {msg}"
    ) from last_exc


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
