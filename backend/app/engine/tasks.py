import logging
from datetime import date, datetime, time, timedelta

from sqlalchemy import func, select, text
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import SQLAlchemyError

from app.config import settings
from app.db.connection import get_db
from app.db.models import (
    BaselineJobLog,
    BaselineJobLogItem,
    DailyBaseline,
    DailyMarketSnapshot,
    Position,
    PositionLedger,
    Watchlist,
)
from app.engine.market_hours import (
    SH_TZ,
    is_in_trading_session,
    is_trading_day,
)
from app.engine.resilience import (
    record_poll_round_failure,
    record_poll_round_success,
)
from app.engine.state import runtime_state
from app.market_signal import (
    DEFAULT_VOLUME_LOOKBACK,
    SIGNAL_ADDON,
    SIGNAL_BUY,
    SIGNAL_PARTIAL_TP,
    SIGNAL_SELL,
    SIGNAL_STOP_LOSS,
    SIGNAL_TAKE_PROFIT,
    BaselineWindow,
    InsufficientDataError,
    SignalRequest,
    StaticBaselineFeed,
    StrategyParams,
    VolumeWindow,
    compute_baseline,
    compute_volume_window,
    evaluate,
    get_market_signal_strategy,
    risk_params_from_resolved,
)
from app.services.alert_service import (
    is_limit_up,
    process_buy_candidates,
    process_risk_candidates,
    process_sell_candidates,
)
from app.services.market_data.numbers import coerce_optional_float, parse_float
from app.services.market_data_service import (
    MarketDataUnavailableError,
    MissingTradeDayBarError,
    StockDataFetchError,
    fetch_daily_bars,
    fetch_qfq_bars_range,
    fetch_realtime_quotes_batch,
    fetch_trade_day_bar,
)
from app.services.strategy_service import resolve_params
from app.services.watchlist_service import (
    restore_halted_to_normal,
    update_watchlist_status,
)

logger = logging.getLogger(__name__)

JOB_NAME = "daily-baseline-precompute"

# Re-export for existing tests / callers.
__all__ = [
    "baseline_precompute_task",
    "bootstrap_watchlist_snapshot",
    "daily_snapshot_task",
    "intraday_snapshot_task",
    "is_in_trading_session",
    "is_trading_day",
    "market_polling_task",
    "startup_market_data_catchup",
    "upsert_snapshot",
    "upsert_qfq_bars",
    "get_snapshot",
]


def _create_job_log(job_name: str, trade_date: str) -> int:
    with get_db() as session:
        row = BaselineJobLog(
            job_name=job_name,
            trade_date=trade_date,
            status="RUNNING",
            started_at=datetime.now(),
        )
        session.add(row)
        session.commit()
        return int(row.id)


def _finish_job_log(
    job_log_id: int,
    status: str,
    total_count: int,
    success_count: int,
    failed_count: int,
    error_summary: str | None = None,
) -> None:
    now = datetime.now()
    with get_db() as session:
        row = session.get(BaselineJobLog, job_log_id)
        if row is None:
            return
        row.status = status
        row.finished_at = now
        row.total_count = total_count
        row.success_count = success_count
        row.failed_count = failed_count
        row.error_summary = error_summary
        row.updated_at = now
        session.commit()


def _insert_log_item(
    job_log_id: int,
    stock_code: str,
    stock_name: str | None,
    strategy_n: int,
    actual_n: int | None,
    status: str,
    low_min: float | None = None,
    high_max: float | None = None,
    error_message: str | None = None,
) -> None:
    with get_db() as session:
        session.add(
            BaselineJobLogItem(
                job_log_id=job_log_id,
                stock_code=stock_code,
                stock_name=stock_name,
                strategy_n=strategy_n,
                actual_n=actual_n,
                status=status,
                low_min=low_min,
                high_max=high_max,
                error_message=error_message,
            )
        )
        session.commit()


def upsert_baseline(
    stock_code: str, trade_date: str, low_min: float, high_max: float, actual_n: int
) -> None:
    now = datetime.now()
    with get_db() as session:
        stmt = (
            sqlite_insert(DailyBaseline)
            .values(
                stock_code=stock_code,
                trade_date=trade_date,
                low_min=low_min,
                high_max=high_max,
                actual_n=actual_n,
                updated_at=now,
            )
            .on_conflict_do_update(
                index_elements=["stock_code", "trade_date"],
                set_={
                    "low_min": low_min,
                    "high_max": high_max,
                    "actual_n": actual_n,
                    "updated_at": now,
                },
            )
        )
        session.execute(stmt)
        session.commit()


def get_baselines_by_date(trade_date: str) -> list[dict]:
    with get_db() as session:
        rows = session.scalars(
            select(DailyBaseline).where(DailyBaseline.trade_date == trade_date)
        ).all()
        return [
            {
                "stock_code": r.stock_code,
                "trade_date": r.trade_date,
                "low_min": r.low_min,
                "high_max": r.high_max,
                "actual_n": r.actual_n,
                "updated_at": r.updated_at,
            }
            for r in rows
        ]


def upsert_snapshot(
    stock_code: str,
    trade_date: str,
    open_price: float,
    high_price: float,
    low_price: float,
    close_price: float,
    volume: float,
    turnover_rate: float | None = None,
) -> None:
    """Insert or update a daily market snapshot (PK: stock_code + trade_date)."""
    now = datetime.now()
    with get_db() as session:
        stmt = (
            sqlite_insert(DailyMarketSnapshot)
            .values(
                stock_code=stock_code,
                trade_date=trade_date,
                open_price=open_price,
                high_price=high_price,
                low_price=low_price,
                close_price=close_price,
                volume=volume,
                turnover_rate=turnover_rate,
                updated_at=now,
            )
            .on_conflict_do_update(
                index_elements=["stock_code", "trade_date"],
                set_={
                    "open_price": open_price,
                    "high_price": high_price,
                    "low_price": low_price,
                    "close_price": close_price,
                    "volume": volume,
                    "turnover_rate": turnover_rate,
                    "updated_at": now,
                },
            )
        )
        session.execute(stmt)
        session.commit()


def upsert_qfq_bars(stock_code: str, bars: list[dict]) -> int:
    """Bulk-upsert **forward-adjusted (qfq)** daily bars into ``daily_market_snapshots``.

    Thin batch wrapper around :func:`upsert_snapshot`; this is the single write
    entrypoint the backtest worker uses to backfill missing history (design.md
    "唯一写入口" contract). Callers must pass qfq OHLC — unadjusted history
    must not be written here. Returns the number of bars written.
    """
    written = 0
    for bar in bars:
        date_value = bar.get("date")
        open_price = bar.get("open")
        high_price = bar.get("high")
        low_price = bar.get("low")
        close_price = bar.get("close")
        volume = bar.get("volume")
        if None in (date_value, open_price, high_price, low_price, close_price, volume):
            continue
        upsert_snapshot(
            stock_code=stock_code,
            trade_date=str(date_value)[:10],
            open_price=float(open_price),
            high_price=float(high_price),
            low_price=float(low_price),
            close_price=float(close_price),
            volume=float(volume),
            turnover_rate=(
                float(bar["turnover_rate"]) if bar.get("turnover_rate") is not None else None
            ),
        )
        written += 1
    return written


def _intraday_snapshot_due(now: datetime) -> bool:
    """True when polling should persist today's approximate OHLCV (throttled)."""
    last = runtime_state.last_intraday_snapshot_at
    if last is None:
        return True
    interval = max(1, int(settings.intraday_snapshot_interval_seconds))
    return (now - last).total_seconds() >= interval


def _upsert_intraday_snapshots_from_quotes(
    trade_date: str, quotes: dict[str, dict], codes: list[str]
) -> tuple[int, int]:
    """UPSERT today's snapshot from already-fetched realtime quotes.

    Returns (success_count, skipped_count).
    """
    success_count = 0
    skipped_count = 0
    for code in codes:
        quote = quotes.get(code) or {}
        try:
            if _upsert_snapshot_from_quote(code, trade_date, quote):
                success_count += 1
            else:
                skipped_count += 1
        except Exception as exc:  # noqa: BLE001
            skipped_count += 1
            logger.warning(
                "intraday_snapshot upsert failed for %s: %s",
                code,
                exc,
            )
    return success_count, skipped_count


def _upsert_snapshot_from_quote(stock_code: str, trade_date: str, quote: dict) -> bool:
    """Write/update a snapshot row from a Sina realtime quote. Returns True on success."""
    price = quote.get("price")
    if "has_quote" in quote:
        has_quote = bool(quote["has_quote"])
    else:
        has_quote = price is not None
    try:
        close_price = parse_float(price)
    except (TypeError, ValueError):
        return False
    if not has_quote or close_price <= 0:
        return False

    open_price = coerce_optional_float(quote.get("open"))
    if open_price is None or open_price <= 0:
        open_price = close_price
    high_price = coerce_optional_float(quote.get("high"))
    if high_price is None or high_price <= 0:
        high_price = max(open_price, close_price)
    low_price = coerce_optional_float(quote.get("low"))
    if low_price is None or low_price <= 0:
        low_price = min(open_price, close_price)
    volume = coerce_optional_float(quote.get("volume"))
    if volume is None:
        volume = 0.0

    upsert_snapshot(
        stock_code=stock_code,
        trade_date=trade_date,
        open_price=open_price,
        high_price=high_price,
        low_price=low_price,
        close_price=close_price,
        volume=volume,
        turnover_rate=None,
    )
    return True


def _bootstrap_from_daily_bars(stock_code: str, *, lookback_days: int = 5) -> bool:
    """Fallback: find the nearest available qfq daily bar and upsert it."""
    today = datetime.now(SH_TZ).date()
    for offset in range(lookback_days):
        candidate: date = today - timedelta(days=offset)
        if not is_trading_day(candidate):
            continue
        try:
            bar = fetch_trade_day_bar(
                stock_code,
                candidate,
                retries=settings.snapshot_fetch_retries,
                retry_backoff_seconds=settings.snapshot_fetch_retry_backoff_seconds,
            )
            upsert_snapshot(
                stock_code=stock_code,
                trade_date=candidate.isoformat(),
                open_price=float(bar["open"]),
                high_price=float(bar["high"]),
                low_price=float(bar["low"]),
                close_price=float(bar["close"]),
                volume=float(bar["volume"]),
                turnover_rate=(
                    float(bar["turnover_rate"])
                    if bar.get("turnover_rate") is not None
                    else None
                ),
            )
            return True
        except MissingTradeDayBarError:
            continue
        except (MarketDataUnavailableError, StockDataFetchError) as exc:
            logger.warning(
                "bootstrap_watchlist_snapshot daily-bar fallback failed for %s on %s: %s",
                stock_code,
                candidate.isoformat(),
                exc,
            )
            return False
    return False


def bootstrap_watchlist_snapshot(stock_code: str) -> None:
    """Fetch and persist a market snapshot after a stock is added to the watchlist."""
    today = datetime.now(SH_TZ).date()
    trade_date = today.isoformat()
    try:
        quotes = fetch_realtime_quotes_batch(
            [stock_code],
            timeout_seconds=settings.polling_request_timeout_seconds,
            retries=settings.polling_request_retries,
            retry_backoff_seconds=settings.polling_request_retry_backoff_seconds,
        )
        quote = quotes.get(stock_code) or {}
        quote_date = quote.get("quote_date")
        if quote_date:
            trade_date = str(quote_date)
        if _upsert_snapshot_from_quote(stock_code, trade_date, quote):
            logger.info(
                "bootstrap_watchlist_snapshot upserted %s from realtime trade_date=%s",
                stock_code,
                trade_date,
            )
            return
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "bootstrap_watchlist_snapshot realtime failed for %s: %s",
            stock_code,
            exc,
        )

    try:
        if _bootstrap_from_daily_bars(stock_code):
            logger.info(
                "bootstrap_watchlist_snapshot upserted %s from daily bar fallback",
                stock_code,
            )
        else:
            logger.warning(
                "bootstrap_watchlist_snapshot found no snapshot data for %s",
                stock_code,
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "bootstrap_watchlist_snapshot failed for %s: %s",
            stock_code,
            exc,
        )


def intraday_snapshot_task() -> None:
    """One-shot refresh of today's snapshots from realtime quotes.

    Used by startup catch-up. Periodic writes are folded into ``market_polling_task``
    and throttled by ``intraday_snapshot_interval_seconds``.
    """
    now = datetime.now(SH_TZ)
    today = now.date()
    trade_date = today.isoformat()

    if not is_trading_day(today):
        return
    if not is_in_trading_session(now.time()):
        return

    with get_db() as session:
        rows = session.scalars(
            select(Watchlist)
            .where(Watchlist.status == "NORMAL")
            .order_by(Watchlist.stock_code)
        ).all()
        codes = [r.stock_code for r in rows]

    if not codes:
        return

    batch_size = max(1, settings.polling_batch_size)
    success_count = 0
    skipped_count = 0

    for start in range(0, len(codes), batch_size):
        batch = codes[start : start + batch_size]
        try:
            quotes = fetch_realtime_quotes_batch(
                batch,
                timeout_seconds=settings.polling_request_timeout_seconds,
                retries=settings.polling_request_retries,
                retry_backoff_seconds=settings.polling_request_retry_backoff_seconds,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "intraday_snapshot_task batch fetch failed codes=%s error=%s",
                ",".join(batch[:5]),
                exc,
            )
            skipped_count += len(batch)
            continue

        written, skipped = _upsert_intraday_snapshots_from_quotes(
            trade_date, quotes, batch
        )
        success_count += written
        skipped_count += skipped

    runtime_state.last_intraday_snapshot_at = now
    logger.info(
        "intraday_snapshot_task finished trade_date=%s total=%d success=%d skipped=%d",
        trade_date,
        len(codes),
        success_count,
        skipped_count,
    )


def get_snapshot(stock_code: str, trade_date: str) -> dict | None:
    with get_db() as session:
        row = session.get(DailyMarketSnapshot, (stock_code, trade_date))
        if row is None:
            return None
        return {
            "stock_code": row.stock_code,
            "trade_date": row.trade_date,
            "open_price": row.open_price,
            "high_price": row.high_price,
            "low_price": row.low_price,
            "close_price": row.close_price,
            "volume": row.volume,
            "turnover_rate": row.turnover_rate,
            "updated_at": row.updated_at,
        }


def baseline_precompute_task() -> None:
    """Pre-market baseline job using forward-adjusted (qfq) daily bars.

    Also attempts to restore HALT stocks to NORMAL for the new session so
    monitoring can resume after prior halt markings (user story 12).
    """
    logger.info("baseline_precompute_task triggered")
    today = datetime.now(SH_TZ).date().isoformat()

    runtime_state.job_status = "RUNNING"

    job_log_id = _create_job_log(JOB_NAME, today)

    # Load NORMAL + HALT (exclude DELISTED). HALT may resume after pre-market.
    with get_db() as session:
        rows = session.execute(
            text(
                """
                SELECT w.stock_code, w.stock_name, w.status,
                       COALESCE(
                           w.custom_n,
                           CASE
                               WHEN sc.global_buy_n >= sc.global_sell_n THEN sc.global_buy_n
                               ELSE sc.global_sell_n
                           END
                       ) AS effective_n
                FROM watchlist w
                JOIN strategy_config sc ON sc.id = 1
                WHERE w.status IN ('NORMAL', 'HALT')
                """
            )
        ).mappings().all()
    stocks = [dict(r) for r in rows]

    total = len(stocks)
    success_count = 0
    failed_count = 0
    restored_codes: list[str] = []
    errors: list[str] = []

    for stock in stocks:
        code = stock["stock_code"]
        name = stock["stock_name"]
        n = stock["effective_n"]
        prior_status = stock["status"]

        try:
            bars = fetch_daily_bars(code, n)
            low_min, high_max, actual_n = compute_baseline(bars, n)
            upsert_baseline(code, today, low_min, high_max, actual_n)
            try:
                upsert_qfq_bars(code, bars)
            except Exception as cache_exc:  # noqa: BLE001
                logger.warning(
                    "baseline_precompute snapshot cache failed stock=%s error=%s",
                    code,
                    cache_exc,
                )
            vol = compute_volume_window(bars, DEFAULT_VOLUME_LOOKBACK)

            # Update in-memory cache
            runtime_state.baseline_cache[code] = {
                "low_min": low_min,
                "high_max": high_max,
                "actual_n": actual_n,
                "trade_date": today,
                "avg_volume_7d": vol.avg_volume,
                "volume_lookback_n": vol.actual_n,
            }

            if prior_status == "HALT":
                restored_codes.append(code)
                logger.info(
                    "halt_resume_candidate stock=%s trade_date=%s actual_n=%d",
                    code,
                    today,
                    actual_n,
                )

            try:
                _insert_log_item(
                    job_log_id, code, name, n, actual_n, "SUCCESS", low_min, high_max
                )
            except SQLAlchemyError as log_exc:
                logger.error("Failed to write log item for %s: %s", code, log_exc)

            if actual_n < n:
                logger.info(
                    "baseline_degraded stock=%s strategy_n=%d actual_n=%d "
                    "insufficient_days=%d",
                    code,
                    n,
                    actual_n,
                    n - actual_n,
                )

            success_count += 1

        except MarketDataUnavailableError as exc:
            # Global failure - mark all remaining as failed
            logger.error(
                "baseline_market_data_unavailable stock=%s error=%s", code, exc
            )
            failed_count += total - success_count
            errors.append(f"Market data unavailable: {exc}")
            try:
                _insert_log_item(
                    job_log_id, code, name, n, None, "FAILED", error_message=str(exc)
                )
            except SQLAlchemyError as log_exc:
                logger.error(
                    "Failed to write failed log item for %s: %s", code, log_exc
                )
            break

        except (StockDataFetchError, InsufficientDataError) as exc:
            logger.warning(
                "baseline_stock_failed stock=%s error=%s", code, exc
            )
            failed_count += 1
            errors.append(f"{code}: {exc}")
            try:
                _insert_log_item(
                    job_log_id, code, name, n, None, "FAILED", error_message=str(exc)
                )
            except SQLAlchemyError as log_exc:
                logger.error(
                    "Failed to write failed log item for %s: %s", code, log_exc
                )

        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "baseline_unexpected_error stock=%s error=%s", code, exc
            )
            failed_count += 1
            errors.append(f"{code}: {exc}")
            try:
                _insert_log_item(
                    job_log_id, code, name, n, None, "FAILED", error_message=str(exc)
                )
            except SQLAlchemyError as log_exc:
                logger.error(
                    "Failed to write failed log item for %s: %s", code, log_exc
                )

    if restored_codes:
        restored = restore_halted_to_normal(restored_codes)
        logger.info(
            "halt_status_restored count=%d codes=%s",
            restored,
            ",".join(restored_codes[:20]),
        )

    final_status = "FAILED" if failed_count > 0 else "SUCCESS"
    error_summary = "; ".join(errors[:10]) if errors else None

    _finish_job_log(
        job_log_id, final_status, total, success_count, failed_count, error_summary
    )

    runtime_state.job_status = final_status
    logger.info(
        "baseline_precompute_task finished: %s (success=%d, failed=%d)",
        final_status,
        success_count,
        failed_count,
    )


def market_polling_task() -> None:
    now = datetime.now(SH_TZ)
    today = now.date()
    trade_date = today.isoformat()
    current_time = now.time()

    if not is_trading_day(today):
        logger.debug("market_polling_task skipped: not trading day (%s)", trade_date)
        return
    if not is_in_trading_session(current_time):
        logger.debug(
            "market_polling_task skipped: out of session (%s)", current_time.isoformat()
        )
        return

    _sync_signal_trade_date(trade_date)
    if not _ensure_baseline_cache_for_trade_date(trade_date):
        if runtime_state.no_baseline_warned_date != trade_date:
            logger.warning("market_polling_task skipped: no baselines for %s", trade_date)
            runtime_state.no_baseline_warned_date = trade_date
        return

    _load_position_cache()

    with get_db() as session:
        rows = session.execute(
            text(
                """
                SELECT
                    w.stock_code,
                    w.stock_name,
                    COALESCE(w.custom_x, sc.global_buy_x) AS effective_x,
                    COALESCE(w.custom_y, sc.global_sell_y) AS effective_y
                FROM watchlist w
                JOIN strategy_config sc ON sc.id = 1
                WHERE w.status = 'NORMAL'
                """
            )
        ).mappings().all()

    stocks = [dict(row) for row in rows]
    if not stocks:
        logger.info("market_polling_task: no NORMAL stocks")
        return

    valid_stocks: list[dict] = []
    skipped_no_baseline = 0
    for stock in stocks:
        baseline = runtime_state.baseline_cache.get(stock["stock_code"])
        if not baseline or baseline.get("trade_date") != trade_date:
            skipped_no_baseline += 1
            continue
        valid_stocks.append(stock)

    if not valid_stocks:
        logger.warning(
            "market_polling_task: no valid stocks with baseline (trade_date=%s)",
            trade_date,
        )
        return

    strategy = get_market_signal_strategy()
    batch_size = max(1, min(50, settings.polling_batch_size))
    total_batches = (len(valid_stocks) + batch_size - 1) // batch_size

    batch_errors = 0
    batches_attempted = 0
    success_quotes = 0
    skipped_invalid_quote = 0
    skipped_halted = 0
    halted_persisted = 0
    buy_candidates = 0
    sell_candidates = 0
    risk_candidates = 0
    candidates: list[dict] = []
    write_snapshots = _intraday_snapshot_due(now)
    snapshot_success = 0
    snapshot_skipped = 0

    for i in range(0, len(valid_stocks), batch_size):
        batch = valid_stocks[i : i + batch_size]
        codes = [item["stock_code"] for item in batch]
        batches_attempted += 1
        try:
            quotes = fetch_realtime_quotes_batch(
                codes,
                timeout_seconds=settings.polling_request_timeout_seconds,
                retries=settings.polling_request_retries,
                retry_backoff_seconds=settings.polling_request_retry_backoff_seconds,
            )
        except (MarketDataUnavailableError, StockDataFetchError, ValueError) as exc:
            batch_errors += 1
            logger.warning(
                "market_polling_batch_failed codes=%s error=%s",
                ",".join(codes),
                exc,
            )
            if write_snapshots:
                snapshot_skipped += len(batch)
            continue

        if write_snapshots:
            written, skipped = _upsert_intraday_snapshots_from_quotes(
                trade_date, quotes, codes
            )
            snapshot_success += written
            snapshot_skipped += skipped

        for stock in batch:
            code = stock["stock_code"]
            quote = quotes.get(code) or {}
            price = quote.get("price")
            quote_date = quote.get("quote_date")
            is_halted = bool(quote.get("is_halted"))
            if "has_quote" in quote:
                has_quote = bool(quote["has_quote"])
            else:
                # Backward-compatible for callers/tests that omit has_quote.
                has_quote = price is not None

            # Persist HALT only when the provider returned a quote payload indicating halt.
            if is_halted and has_quote:
                skipped_halted += 1
                try:
                    updated = update_watchlist_status(code, "HALT")
                    if updated:
                        halted_persisted += 1
                        logger.info(
                            "watchlist_status_halted stock=%s trade_date=%s",
                            code,
                            trade_date,
                        )
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "watchlist_halt_persist_failed stock=%s error=%s", code, exc
                    )
                continue

            if not has_quote or price is None or price <= 0 or quote_date != trade_date:
                skipped_invalid_quote += 1
                continue

            success_quotes += 1

            baseline = runtime_state.baseline_cache[code]
            low_min = float(baseline["low_min"])
            high_max = float(baseline["high_max"])
            effective_x = float(stock["effective_x"])
            effective_y = float(stock["effective_y"])
            prev_close = quote.get("prev_close")
            pos = runtime_state.position_cache.get(code) or {}
            qty = int(pos.get("qty") or 0)
            holding = qty > 0
            quote_volume = quote.get("volume")
            volume = float(quote_volume) if quote_volume is not None else None
            avg_volume = float(baseline.get("avg_volume_7d") or 0.0)
            volume_n = int(baseline.get("volume_lookback_n") or 0)
            decision = strategy.evaluate(
                SignalRequest(
                    price=float(price),
                    volume=volume,
                    feed=StaticBaselineFeed(
                        BaselineWindow(
                            low_min=low_min,
                            high_max=high_max,
                            actual_n=int(baseline["actual_n"]),
                        ),
                        VolumeWindow(
                            avg_volume=avg_volume,
                            actual_n=volume_n,
                            lookback=DEFAULT_VOLUME_LOOKBACK,
                        ),
                    ),
                    params=StrategyParams(x=effective_x, y=effective_y),
                )
            )

            if holding:
                avg_cost = pos.get("avg_cost")
                if avg_cost is None or float(avg_cost) <= 0:
                    continue

                params = resolve_params(code)

                # Exit already fired today — skip further risk evaluation.
                if code not in runtime_state.exit_fired_today:
                    risk = risk_params_from_resolved(params)
                    prev_ladder = runtime_state.partial_tp_ladder_idx.get(code)
                    result = evaluate(
                        avg_cost=float(avg_cost),
                        highest_since_hold=pos.get("highest_since_hold"),
                        prev_stop=pos.get("stop_price"),
                        price=float(price),
                        params=risk,
                        prev_ladder_idx=prev_ladder,
                    )

                    changed = (
                        pos.get("highest_since_hold") != result.highest_since_hold
                        or pos.get("stop_price") != result.stop_price
                    )
                    if changed:
                        try:
                            _persist_position_risk(
                                code,
                                highest=result.highest_since_hold,
                                stop_price=result.stop_price,
                            )
                        except Exception as exc:  # noqa: BLE001
                            logger.warning(
                                "position_risk_persist_failed stock=%s error=%s",
                                code,
                                exc,
                            )

                    if result.ladder_idx is not None:
                        runtime_state.partial_tp_ladder_idx[code] = result.ladder_idx

                    if result.exit_signal:
                        t1_note = _has_same_day_buy(code, trade_date)
                        runtime_state.set_signal(
                            code, result.exit_signal, t1_note=t1_note
                        )
                        runtime_state.exit_fired_today.add(code)
                        risk_candidates += 1
                        candidates.append(
                            {
                                "stock_code": code,
                                "stock_name": stock["stock_name"],
                                "signal_type": result.exit_signal,
                                "price": float(price),
                                "trade_date": trade_date,
                                "baseline_price": result.stop_price,
                                "used_coeff": 1.0,
                                "actual_n": int(baseline["actual_n"]),
                                "prev_close": prev_close,
                                "stop_price": result.stop_price,
                                "avg_cost": float(avg_cost),
                                "highest_since_hold": result.highest_since_hold,
                                "pnl_pct": result.pnl_pct,
                                "t1_note": t1_note,
                            }
                        )
                    elif result.partial_tp is not None:
                        runtime_state.set_signal(code, SIGNAL_PARTIAL_TP)
                        risk_candidates += 1
                        candidates.append(
                            {
                                "stock_code": code,
                                "stock_name": stock["stock_name"],
                                "signal_type": SIGNAL_PARTIAL_TP,
                                "price": float(price),
                                "trade_date": trade_date,
                                "baseline_price": result.stop_price,
                                "used_coeff": result.partial_tp.suggest_sell_pct,
                                "actual_n": int(baseline["actual_n"]),
                                "prev_close": prev_close,
                                "stop_price": result.stop_price,
                                "avg_cost": float(avg_cost),
                                "highest_since_hold": result.highest_since_hold,
                                "pnl_pct": result.partial_tp.pnl_pct,
                                "suggest_sell_pct": result.partial_tp.suggest_sell_pct,
                            }
                        )

                # Holding: optional ADDON / tech SELL (never plain BUY).
                # Do not overwrite an exit signal already set for the list/UI.
                current_signal = runtime_state.signal_state.get(code)
                if current_signal not in {SIGNAL_STOP_LOSS, SIGNAL_TAKE_PROFIT}:
                    if params["enable_addon_alert"] and decision.addon:
                        addon_ref = decision.buy_reference
                        runtime_state.set_signal(code, SIGNAL_ADDON)
                        risk_candidates += 1
                        candidates.append(
                            {
                                "stock_code": code,
                                "stock_name": stock["stock_name"],
                                "signal_type": SIGNAL_ADDON,
                                "price": float(price),
                                "trade_date": trade_date,
                                "baseline_price": addon_ref.baseline_price,
                                "used_coeff": addon_ref.used_coeff,
                                "actual_n": int(baseline["actual_n"]),
                                "prev_close": prev_close,
                            }
                        )
                    elif params["enable_tech_sell_while_holding"] and decision.sell:
                        sell_ref = decision.sell_reference
                        limit_flag = is_limit_up(
                            stock_code=code,
                            price=float(price),
                            prev_close=float(prev_close)
                            if prev_close is not None
                            else None,
                            stock_name=str(stock["stock_name"] or ""),
                        )
                        runtime_state.set_signal(
                            code, SIGNAL_SELL, is_limit_up=limit_flag
                        )
                        sell_candidates += 1
                        candidates.append(
                            {
                                "stock_code": code,
                                "stock_name": stock["stock_name"],
                                "signal_type": SIGNAL_SELL,
                                "price": float(price),
                                "trade_date": trade_date,
                                "baseline_price": sell_ref.baseline_price,
                                "used_coeff": sell_ref.used_coeff,
                                "actual_n": int(baseline["actual_n"]),
                                "prev_close": prev_close,
                                "is_limit_up": limit_flag,
                            }
                        )
                continue

            # EMPTY mode — V1 BUY/SELL
            signal_type: str | None = None
            if decision.buy:
                signal_type = SIGNAL_BUY
            if decision.sell:
                signal_type = SIGNAL_SELL

            if signal_type:
                if signal_type == SIGNAL_BUY:
                    reference = decision.buy_reference
                    buy_candidates += 1
                    runtime_state.set_signal(code, signal_type)
                else:
                    reference = decision.sell_reference
                    sell_candidates += 1
                    limit_flag = is_limit_up(
                        stock_code=code,
                        price=float(price),
                        prev_close=float(prev_close)
                        if prev_close is not None
                        else None,
                        stock_name=str(stock["stock_name"] or ""),
                    )
                    runtime_state.set_signal(
                        code, signal_type, is_limit_up=limit_flag
                    )
                candidate: dict = {
                    "stock_code": code,
                    "stock_name": stock["stock_name"],
                    "signal_type": signal_type,
                    "price": price,
                    "trade_date": trade_date,
                    "baseline_price": reference.baseline_price,
                    "used_coeff": reference.used_coeff,
                    "actual_n": int(baseline["actual_n"]),
                    "prev_close": prev_close,
                }
                if signal_type == SIGNAL_SELL:
                    candidate["is_limit_up"] = runtime_state.signal_meta.get(
                        code, {}
                    ).get("is_limit_up", False)
                candidates.append(candidate)

    if batches_attempted > 0 and batch_errors == batches_attempted:
        record_poll_round_failure(
            trade_date=trade_date,
            reason=f"all {batches_attempted} realtime batch(es) failed",
        )
    elif batch_errors < batches_attempted:
        # At least one batch succeeded — clear delay / failure streak.
        record_poll_round_success()

    _emit_signal_candidates(candidates)

    if write_snapshots:
        runtime_state.last_intraday_snapshot_at = now
        logger.info(
            "market_polling_task snapshot trade_date=%s success=%d skipped=%d",
            trade_date,
            snapshot_success,
            snapshot_skipped,
        )

    logger.info(
        "market_polling_task summary trade_date=%s batches=%d batch_errors=%d "
        "success_quotes=%d skipped_no_baseline=%d skipped_halted=%d "
        "halted_persisted=%d skipped_invalid_quote=%d buy_candidates=%d "
        "sell_candidates=%d risk_candidates=%d quote_delay=%s consecutive_failures=%d",
        trade_date,
        total_batches,
        batch_errors,
        success_quotes,
        skipped_no_baseline,
        skipped_halted,
        halted_persisted,
        skipped_invalid_quote,
        buy_candidates,
        sell_candidates,
        risk_candidates,
        runtime_state.quote_delay,
        runtime_state.consecutive_poll_failures,
    )


def _sync_signal_trade_date(trade_date: str) -> None:
    if runtime_state.signal_trade_date != trade_date:
        runtime_state.signal_trade_date = trade_date
        runtime_state.signal_state.clear()
        runtime_state.signal_meta.clear()
        # Keys include trade_date; clear to keep the O(1) set bounded across days.
        runtime_state.sent_signal_keys.clear()
        runtime_state.daily_cap_reached.clear()
        runtime_state.partial_tp_ladder_idx.clear()
        runtime_state.exit_fired_today.clear()


def _ensure_baseline_cache_for_trade_date(trade_date: str) -> bool:
    has_today = any(
        item.get("trade_date") == trade_date
        for item in runtime_state.baseline_cache.values()
    )
    if has_today:
        return True

    runtime_state.baseline_cache.clear()
    baselines = get_baselines_by_date(trade_date)
    codes = [row["stock_code"] for row in baselines]
    avg_map = _avg_volumes_before(codes, trade_date)
    for row in baselines:
        avg_volume, volume_n = avg_map.get(row["stock_code"], (0.0, 0))
        runtime_state.baseline_cache[row["stock_code"]] = {
            "low_min": row["low_min"],
            "high_max": row["high_max"],
            "actual_n": row["actual_n"],
            "trade_date": row["trade_date"],
            "avg_volume_7d": avg_volume,
            "volume_lookback_n": volume_n,
        }
    return bool(runtime_state.baseline_cache)


def _avg_volumes_before(
    stock_codes: list[str],
    trade_date: str,
    *,
    lookback: int = DEFAULT_VOLUME_LOOKBACK,
) -> dict[str, tuple[float, int]]:
    """Mean volume of the last ``lookback`` snapshot days strictly before ``trade_date``."""
    if not stock_codes or lookback <= 0:
        return {}
    with get_db() as session:
        ranked = (
            select(
                DailyMarketSnapshot.stock_code,
                DailyMarketSnapshot.volume,
                func.row_number()
                .over(
                    partition_by=DailyMarketSnapshot.stock_code,
                    order_by=DailyMarketSnapshot.trade_date.desc(),
                )
                .label("rn"),
            )
            .where(
                DailyMarketSnapshot.stock_code.in_(stock_codes),
                DailyMarketSnapshot.trade_date < trade_date,
                DailyMarketSnapshot.volume > 0,
            )
            .subquery()
        )
        rows = session.execute(
            select(ranked.c.stock_code, ranked.c.volume).where(ranked.c.rn <= lookback)
        ).all()
    grouped: dict[str, list[float]] = {}
    for code, volume in rows:
        grouped.setdefault(code, []).append(float(volume))
    return {
        code: (sum(vols) / len(vols), len(vols))
        for code, vols in grouped.items()
        if vols
    }


def _load_position_cache() -> None:
    with get_db() as session:
        rows = session.scalars(select(Position)).all()
        runtime_state.position_cache = {
            row.stock_code: {
                "qty": int(row.qty),
                "avg_cost": row.avg_cost,
                "highest_since_hold": row.highest_since_hold,
                "stop_price": row.stop_price,
                "position_status": row.position_status,
            }
            for row in rows
        }


def _persist_position_risk(
    stock_code: str, *, highest: float, stop_price: float
) -> None:
    with get_db() as session:
        pos = session.get(Position, stock_code)
        if pos is None or pos.qty <= 0:
            return
        pos.highest_since_hold = highest
        pos.stop_price = stop_price
        pos.updated_at = datetime.now(SH_TZ)
        session.commit()
    cached = runtime_state.position_cache.get(stock_code)
    if cached is not None:
        cached["highest_since_hold"] = highest
        cached["stop_price"] = stop_price


def _has_same_day_buy(stock_code: str, trade_date: str) -> bool:
    with get_db() as session:
        row = (
            session.query(PositionLedger.id)
            .filter(
                PositionLedger.stock_code == stock_code,
                PositionLedger.side == "BUY",
                PositionLedger.trade_date == trade_date,
            )
            .first()
        )
        return row is not None


def _emit_signal_candidates(candidates: list[dict]) -> None:
    if not candidates:
        return
    logger.info("market_polling_task candidates count=%d", len(candidates))
    # User stories 08/09: BUY and SELL WeChat alerts (failures isolated per stock).
    process_buy_candidates(candidates)
    process_sell_candidates(candidates)
    # User stories 06/07: risk / partial / addon alerts.
    process_risk_candidates(candidates)


def daily_snapshot_task() -> None:
    """Post-close job: persist OHLCV + turnover snapshots for NORMAL watchlist stocks."""
    today = datetime.now(SH_TZ).date()
    trade_date = today.isoformat()
    logger.info("daily_snapshot_task triggered trade_date=%s", trade_date)

    if not is_trading_day(today):
        logger.info(
            "daily_snapshot_task skipped: not trading day (%s)", trade_date
        )
        return

    with get_db() as session:
        rows = session.scalars(
            select(Watchlist)
            .where(Watchlist.status == "NORMAL")
            .order_by(Watchlist.stock_code)
        ).all()
        stocks = [{"stock_code": r.stock_code, "stock_name": r.stock_name} for r in rows]

    total = len(stocks)
    if total == 0:
        logger.info(
            "daily_snapshot_task finished: no NORMAL stocks (trade_date=%s)",
            trade_date,
        )
        return

    success_count = 0
    failed_count = 0
    skipped_count = 0

    for stock in stocks:
        code = stock["stock_code"]
        name = stock["stock_name"]
        try:
            bar = fetch_trade_day_bar(
                code,
                today,
                retries=settings.snapshot_fetch_retries,
                retry_backoff_seconds=settings.snapshot_fetch_retry_backoff_seconds,
            )
            upsert_snapshot(
                stock_code=code,
                trade_date=trade_date,
                open_price=float(bar["open"]),
                high_price=float(bar["high"]),
                low_price=float(bar["low"]),
                close_price=float(bar["close"]),
                volume=float(bar["volume"]),
                turnover_rate=(
                    float(bar["turnover_rate"])
                    if bar.get("turnover_rate") is not None
                    else None
                ),
            )
            success_count += 1
            logger.debug(
                "daily_snapshot_task upserted %s (%s) close=%.4f",
                code,
                name,
                float(bar["close"]),
            )
        except MissingTradeDayBarError as exc:
            skipped_count += 1
            logger.warning(
                "daily_snapshot_task skipped %s (%s): %s", code, name, exc
            )
        except MarketDataUnavailableError as exc:
            remaining = total - success_count - failed_count - skipped_count
            failed_count += remaining
            logger.error(
                "daily_snapshot_task aborted: market data unavailable at %s: %s "
                "(success=%d failed=%d skipped=%d)",
                code,
                exc,
                success_count,
                failed_count,
                skipped_count,
            )
            break
        except (StockDataFetchError, Exception) as exc:  # noqa: BLE001
            failed_count += 1
            logger.warning(
                "daily_snapshot_task failed %s (%s): %s", code, name, exc
            )

    logger.info(
        "daily_snapshot_task finished trade_date=%s total=%d success=%d failed=%d skipped=%d",
        trade_date,
        total,
        success_count,
        failed_count,
        skipped_count,
    )
    if failed_count > 0:
        logger.error(
            "daily_snapshot_task alert: %d failure(s) on %s (success=%d skipped=%d)",
            failed_count,
            trade_date,
            success_count,
            skipped_count,
        )


def _watchlist_missing_today_snapshots(trade_date: str) -> bool:
    """True when any NORMAL watchlist stock lacks a snapshot for trade_date."""
    with get_db() as session:
        codes = list(
            session.scalars(
                select(Watchlist.stock_code).where(Watchlist.status == "NORMAL")
            ).all()
        )
        if not codes:
            return False
        existing = set(
            session.scalars(
                select(DailyMarketSnapshot.stock_code).where(
                    DailyMarketSnapshot.trade_date == trade_date,
                    DailyMarketSnapshot.stock_code.in_(codes),
                )
            ).all()
        )
    return any(code not in existing for code in codes)


def startup_market_data_catchup() -> None:
    """One-shot catch-up for a missed 08:30 baseline and today's snapshots.

    Scheduled immediately after process start so FastAPI lifespan is not blocked
    by network I/O. No-ops when the corresponding data is already present.
    """
    now = datetime.now(SH_TZ)
    today = now.date()
    trade_date = today.isoformat()
    current_time = now.time()

    if not is_trading_day(today):
        logger.info(
            "startup_market_data_catchup skipped: not trading day (%s)", trade_date
        )
        return

    baseline_cutoff = time(settings.baseline_hour, settings.baseline_minute)
    if current_time >= baseline_cutoff:
        if not get_baselines_by_date(trade_date):
            logger.info(
                "startup_market_data_catchup running missed baseline_precompute for %s",
                trade_date,
            )
            baseline_precompute_task()
        else:
            logger.info(
                "startup_market_data_catchup skip baseline: already present for %s",
                trade_date,
            )

    if is_in_trading_session(current_time):
        logger.info(
            "startup_market_data_catchup running immediate intraday snapshot for %s",
            trade_date,
        )
        intraday_snapshot_task()
        return

    snapshot_cutoff = time(settings.snapshot_hour, settings.snapshot_minute)
    if current_time >= snapshot_cutoff:
        if _watchlist_missing_today_snapshots(trade_date):
            logger.info(
                "startup_market_data_catchup running missed daily_snapshot for %s",
                trade_date,
            )
            daily_snapshot_task()
        else:
            logger.info(
                "startup_market_data_catchup skip daily snapshot: already present for %s",
                trade_date,
            )
