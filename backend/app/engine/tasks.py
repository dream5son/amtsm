import logging
import sqlite3
from datetime import datetime

from app.config import settings
from app.db.baseline_job_log_repo import create_job_log, finish_job_log, insert_log_item
from app.db.connection import get_db
from app.db.daily_baseline_repo import get_baselines_by_date, upsert_baseline
from app.engine.baseline_calculator import InsufficientDataError, compute_baseline
from app.engine.market_hours import (
    SH_TZ,
    is_in_trading_session,
    is_trading_day,
)
from app.engine.state import runtime_state
from app.services.alert_service import (
    is_limit_up,
    process_buy_candidates,
    process_sell_candidates,
)
from app.services.market_data_service import (
    MarketDataUnavailableError,
    StockDataFetchError,
    fetch_daily_bars,
    fetch_realtime_quotes_batch,
)

logger = logging.getLogger(__name__)

JOB_NAME = "daily-baseline-precompute"

# Re-export for existing tests / callers.
__all__ = [
    "baseline_precompute_task",
    "daily_snapshot_task",
    "is_in_trading_session",
    "is_trading_day",
    "market_polling_task",
]


def baseline_precompute_task() -> None:
    logger.info("baseline_precompute_task triggered")
    today = datetime.now(SH_TZ).date().isoformat()

    runtime_state.job_status = "RUNNING"

    job_log_id = create_job_log(JOB_NAME, today)

    # Load all NORMAL stocks with effective N
    with get_db() as conn:
        rows = conn.execute(
            """SELECT w.stock_code, w.stock_name,
                      COALESCE(w.custom_n, CASE WHEN sc.global_buy_n >= sc.global_sell_n THEN sc.global_buy_n ELSE sc.global_sell_n END) AS effective_n
               FROM watchlist w
               JOIN strategy_config sc ON sc.id = 1
               WHERE w.status = 'NORMAL'"""
        ).fetchall()
    stocks = [dict(r) for r in rows]

    total = len(stocks)
    success_count = 0
    failed_count = 0
    errors: list[str] = []

    for stock in stocks:
        code = stock["stock_code"]
        name = stock["stock_name"]
        n = stock["effective_n"]

        try:
            bars = fetch_daily_bars(code, n)
            low_min, high_max, actual_n = compute_baseline(bars, n)
            upsert_baseline(code, today, low_min, high_max, actual_n)

            # Update in-memory cache
            runtime_state.baseline_cache[code] = {
                "low_min": low_min,
                "high_max": high_max,
                "actual_n": actual_n,
                "trade_date": today,
            }

            try:
                insert_log_item(
                    job_log_id, code, name, n, actual_n, "SUCCESS", low_min, high_max
                )
            except sqlite3.Error as log_exc:
                logger.error("Failed to write log item for %s: %s", code, log_exc)

            success_count += 1

        except MarketDataUnavailableError as exc:
            # Global failure - mark all remaining as failed
            logger.error("Market data unavailable: %s", exc)
            failed_count += total - success_count
            errors.append(f"Market data unavailable: {exc}")
            try:
                insert_log_item(
                    job_log_id, code, name, n, None, "FAILED", error_message=str(exc)
                )
            except sqlite3.Error as log_exc:
                logger.error(
                    "Failed to write failed log item for %s: %s", code, log_exc
                )
            break

        except (StockDataFetchError, InsufficientDataError) as exc:
            logger.warning("Failed for %s: %s", code, exc)
            failed_count += 1
            errors.append(f"{code}: {exc}")
            try:
                insert_log_item(
                    job_log_id, code, name, n, None, "FAILED", error_message=str(exc)
                )
            except sqlite3.Error as log_exc:
                logger.error(
                    "Failed to write failed log item for %s: %s", code, log_exc
                )

        except Exception as exc:  # noqa: BLE001
            logger.warning("Unexpected error for %s: %s", code, exc)
            failed_count += 1
            errors.append(f"{code}: {exc}")
            try:
                insert_log_item(
                    job_log_id, code, name, n, None, "FAILED", error_message=str(exc)
                )
            except sqlite3.Error as log_exc:
                logger.error(
                    "Failed to write failed log item for %s: %s", code, log_exc
                )

    final_status = "FAILED" if failed_count > 0 else "SUCCESS"
    error_summary = "; ".join(errors[:10]) if errors else None

    finish_job_log(
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
        logger.warning("market_polling_task skipped: no baselines for %s", trade_date)
        return

    with get_db() as conn:
        rows = conn.execute(
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
        ).fetchall()

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

    batch_size = max(1, min(50, settings.polling_batch_size))
    total_batches = (len(valid_stocks) + batch_size - 1) // batch_size

    batch_errors = 0
    success_quotes = 0
    skipped_invalid_quote = 0
    skipped_halted = 0
    buy_candidates = 0
    sell_candidates = 0
    candidates: list[dict] = []

    for i in range(0, len(valid_stocks), batch_size):
        batch = valid_stocks[i : i + batch_size]
        codes = [item["stock_code"] for item in batch]
        try:
            quotes = fetch_realtime_quotes_batch(
                codes,
                timeout_seconds=settings.polling_request_timeout_seconds,
                retries=settings.polling_request_retries,
            )
        except (MarketDataUnavailableError, StockDataFetchError, ValueError) as exc:
            batch_errors += 1
            logger.warning(
                "market_polling_task batch failed (%s): %s", ",".join(codes), exc
            )
            continue

        for stock in batch:
            code = stock["stock_code"]
            quote = quotes.get(code) or {}
            price = quote.get("price")
            quote_date = quote.get("quote_date")
            is_halted = bool(quote.get("is_halted"))

            if is_halted:
                skipped_halted += 1
                continue
            if price is None or price <= 0 or quote_date != trade_date:
                skipped_invalid_quote += 1
                continue

            success_quotes += 1

            baseline = runtime_state.baseline_cache[code]
            buy_threshold = float(baseline["low_min"]) * float(stock["effective_x"])
            sell_threshold = float(baseline["high_max"]) * float(stock["effective_y"])

            signal_type: str | None = None
            if price <= buy_threshold:
                signal_type = "BUY"
            if price >= sell_threshold:
                signal_type = "SELL"

            if signal_type:
                runtime_state.signal_state[code] = signal_type
                if signal_type == "BUY":
                    baseline_price = float(baseline["low_min"])
                    used_coeff = float(stock["effective_x"])
                    buy_candidates += 1
                else:
                    baseline_price = float(baseline["high_max"])
                    used_coeff = float(stock["effective_y"])
                    sell_candidates += 1
                prev_close = quote.get("prev_close")
                candidate: dict = {
                    "stock_code": code,
                    "stock_name": stock["stock_name"],
                    "signal_type": signal_type,
                    "price": price,
                    "trade_date": trade_date,
                    "baseline_price": baseline_price,
                    "used_coeff": used_coeff,
                    "actual_n": int(baseline["actual_n"]),
                    "prev_close": prev_close,
                }
                if signal_type == "SELL":
                    candidate["is_limit_up"] = is_limit_up(
                        stock_code=code,
                        price=float(price),
                        prev_close=float(prev_close)
                        if prev_close is not None
                        else None,
                        stock_name=str(stock["stock_name"] or ""),
                    )
                candidates.append(candidate)

    _emit_signal_candidates(candidates)

    logger.info(
        "market_polling_task summary trade_date=%s batches=%d batch_errors=%d success_quotes=%d skipped_no_baseline=%d "
        "skipped_halted=%d skipped_invalid_quote=%d buy_candidates=%d sell_candidates=%d",
        trade_date,
        total_batches,
        batch_errors,
        success_quotes,
        skipped_no_baseline,
        skipped_halted,
        skipped_invalid_quote,
        buy_candidates,
        sell_candidates,
    )


def _sync_signal_trade_date(trade_date: str) -> None:
    if runtime_state.signal_trade_date != trade_date:
        runtime_state.signal_trade_date = trade_date
        runtime_state.signal_state.clear()
        # Keys include trade_date; clear to keep the O(1) set bounded across days.
        runtime_state.sent_signal_keys.clear()


def _ensure_baseline_cache_for_trade_date(trade_date: str) -> bool:
    has_today = any(
        item.get("trade_date") == trade_date
        for item in runtime_state.baseline_cache.values()
    )
    if has_today:
        return True

    runtime_state.baseline_cache.clear()
    baselines = get_baselines_by_date(trade_date)
    for row in baselines:
        runtime_state.baseline_cache[row["stock_code"]] = {
            "low_min": row["low_min"],
            "high_max": row["high_max"],
            "actual_n": row["actual_n"],
            "trade_date": row["trade_date"],
        }
    return bool(runtime_state.baseline_cache)


def _emit_signal_candidates(candidates: list[dict]) -> None:
    if not candidates:
        return
    logger.info("market_polling_task candidates count=%d", len(candidates))
    # User stories 08/09: BUY and SELL WeChat alerts (failures isolated per stock).
    process_buy_candidates(candidates)
    process_sell_candidates(candidates)


def daily_snapshot_task() -> None:
    logger.info("daily_snapshot_task triggered")
