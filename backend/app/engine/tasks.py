import logging
from datetime import date

from app.db.baseline_job_log_repo import create_job_log, finish_job_log, insert_log_item
from app.db.daily_baseline_repo import upsert_baseline
from app.db.connection import get_db
from app.engine.baseline_calculator import InsufficientDataError, compute_baseline
from app.engine.state import runtime_state
from app.services.market_data_service import (
    MarketDataUnavailableError,
    StockDataFetchError,
    fetch_daily_bars,
)

logger = logging.getLogger(__name__)

JOB_NAME = "daily-baseline-precompute"


def baseline_precompute_task() -> None:
    logger.info("baseline_precompute_task triggered")
    today = date.today().isoformat()

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
                insert_log_item(job_log_id, code, name, n, actual_n, "SUCCESS", low_min, high_max)
            except Exception as log_exc:
                logger.error("Failed to write log item for %s: %s", code, log_exc)

            success_count += 1

        except MarketDataUnavailableError as exc:
            # Global failure - mark all remaining as failed
            logger.error("Market data unavailable: %s", exc)
            failed_count += total - success_count
            errors.append(f"Market data unavailable: {exc}")
            try:
                insert_log_item(job_log_id, code, name, n, None, "FAILED", error_message=str(exc))
            except Exception:
                pass
            break

        except (StockDataFetchError, InsufficientDataError) as exc:
            logger.warning("Failed for %s: %s", code, exc)
            failed_count += 1
            errors.append(f"{code}: {exc}")
            try:
                insert_log_item(job_log_id, code, name, n, None, "FAILED", error_message=str(exc))
            except Exception:
                pass

        except Exception as exc:
            logger.warning("Unexpected error for %s: %s", code, exc)
            failed_count += 1
            errors.append(f"{code}: {exc}")
            try:
                insert_log_item(job_log_id, code, name, n, None, "FAILED", error_message=str(exc))
            except Exception:
                pass

    final_status = "FAILED" if failed_count > 0 else "SUCCESS"
    error_summary = "; ".join(errors[:10]) if errors else None

    finish_job_log(job_log_id, final_status, total, success_count, failed_count, error_summary)

    runtime_state.job_status = final_status
    logger.info("baseline_precompute_task finished: %s (success=%d, failed=%d)", final_status, success_count, failed_count)


def market_polling_task() -> None:
    logger.info("market_polling_task placeholder")


def daily_snapshot_task() -> None:
    logger.info("daily_snapshot_task triggered")
