from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.db.connection import get_db
from app.db.models import Position, PositionLedger, StockStrategyOverride, Watchlist
from app.engine.state import runtime_state
from app.schemas.watchlist import WatchlistCreate
from app.services.backtest_service import get_watchlist_backtest_summary
from app.services.position_math import stop_distance_pct, unrealized_pnl
from app.services.stock_search_service import normalize_stock_code

_EMPTY_BACKTEST_SUMMARY = {
    "backtest_status": "NONE",
    "backtest_job_id": None,
    "backtest_win_rate": None,
    "backtest_trade_count": None,
    "backtest_sample_insufficient": False,
    "backtest_stale": False,
    "backtest_error_message": None,
}


def list_watchlist(limit: int = 50, offset: int = 0) -> list[dict]:
    if limit < 1 or limit > 200:
        raise ValueError("limit out of range")
    if offset < 0:
        raise ValueError("offset must be >= 0")

    with get_db() as session:
        rows = session.execute(
            text(
                """
                WITH latest_snapshots AS (
                    SELECT dms.stock_code, dms.open_price, dms.close_price
                    FROM daily_market_snapshots dms
                    INNER JOIN (
                        SELECT stock_code, MAX(trade_date) AS max_trade_date
                        FROM daily_market_snapshots
                        GROUP BY stock_code
                    ) latest
                    ON latest.stock_code = dms.stock_code
                    AND latest.max_trade_date = dms.trade_date
                ),
                latest_baselines AS (
                    SELECT db.stock_code, db.actual_n
                    FROM daily_baselines db
                    INNER JOIN (
                        SELECT stock_code, MAX(trade_date) AS max_trade_date
                        FROM daily_baselines
                        GROUP BY stock_code
                    ) latest
                    ON latest.stock_code = db.stock_code
                    AND latest.max_trade_date = db.trade_date
                )
                SELECT
                    w.stock_code,
                    w.stock_name,
                    w.status,
                    w.created_at,
                    ls.close_price AS latest_price,
                    CASE
                        WHEN ls.open_price > 0
                        THEN ROUND((ls.close_price - ls.open_price) * 100.0 / ls.open_price, 2)
                        ELSE NULL
                    END AS change_pct,
                    lb.actual_n,
                    COALESCE(
                        w.custom_n,
                        CASE
                            WHEN sc.global_buy_n >= sc.global_sell_n THEN sc.global_buy_n
                            ELSE sc.global_sell_n
                        END
                    ) AS effective_n,
                    CASE
                        WHEN lb.actual_n IS NULL THEN NULL
                        WHEN COALESCE(
                            w.custom_n,
                            CASE
                                WHEN sc.global_buy_n >= sc.global_sell_n THEN sc.global_buy_n
                                ELSE sc.global_sell_n
                            END
                        ) > lb.actual_n
                        THEN COALESCE(
                            w.custom_n,
                            CASE
                                WHEN sc.global_buy_n >= sc.global_sell_n THEN sc.global_buy_n
                                ELSE sc.global_sell_n
                            END
                        ) - lb.actual_n
                        ELSE 0
                    END AS insufficient_days,
                    COALESCE(p.position_status, 'EMPTY') AS position_status,
                    COALESCE(p.qty, 0) AS position_qty,
                    p.avg_cost,
                    p.stop_price
                FROM watchlist w
                JOIN strategy_config sc ON sc.id = 1
                LEFT JOIN latest_snapshots ls ON ls.stock_code = w.stock_code
                LEFT JOIN latest_baselines lb ON lb.stock_code = w.stock_code
                LEFT JOIN positions p ON p.stock_code = w.stock_code
                ORDER BY w.created_at DESC
                LIMIT :limit OFFSET :offset
                """
            ),
            {"limit": limit, "offset": offset},
        ).mappings().all()
    data = [dict(row) for row in rows]
    backtest_summaries = get_watchlist_backtest_summary([item["stock_code"] for item in data])
    for item in data:
        code = item["stock_code"]
        item["signal_type"] = runtime_state.signal_state.get(code)
        meta = runtime_state.signal_meta.get(code) or {}
        item["signal_t1_note"] = bool(meta.get("t1_note"))
        item["signal_limit_board"] = bool(meta.get("is_limit_up"))
        qty = int(item.get("position_qty") or 0)
        if qty <= 0 or item.get("position_status") == "EMPTY":
            item["position_status"] = "EMPTY"
            item["position_qty"] = 0
            item["avg_cost"] = None
            item["stop_price"] = None
            item["unrealized_pnl"] = None
            item["unrealized_pnl_pct"] = None
            item["stop_distance_pct"] = None
        else:
            pnl, pnl_pct = unrealized_pnl(
                item.get("latest_price"), item.get("avg_cost"), qty
            )
            item["unrealized_pnl"] = pnl
            item["unrealized_pnl_pct"] = pnl_pct
            item["stop_distance_pct"] = stop_distance_pct(
                item.get("latest_price"), item.get("stop_price")
            )
        item.update(backtest_summaries.get(item["stock_code"], _EMPTY_BACKTEST_SUMMARY))
    return data

def add_watchlist(payload: WatchlistCreate) -> None:
    normalized_code = normalize_stock_code(payload.stock_code)

    with get_db() as session:
        try:
            session.add(
                Watchlist(stock_code=normalized_code, stock_name=payload.stock_name)
            )
            session.commit()
        except IntegrityError as exc:
            session.rollback()
            raise ValueError("stock already exists") from exc

    # Lazy import avoids circular import: tasks -> watchlist_service -> scheduler -> tasks
    from app.engine.scheduler import enqueue_snapshot_bootstrap

    enqueue_snapshot_bootstrap(normalized_code)

def remove_watchlist(stock_code: str) -> int:
    normalized_code = normalize_stock_code(stock_code)

    with get_db() as session:
        session.query(PositionLedger).filter(
            PositionLedger.stock_code == normalized_code
        ).delete()
        session.query(Position).filter(Position.stock_code == normalized_code).delete()
        session.query(StockStrategyOverride).filter(
            StockStrategyOverride.stock_code == normalized_code
        ).delete()
        deleted = (
            session.query(Watchlist)
            .filter(Watchlist.stock_code == normalized_code)
            .delete()
        )
        session.commit()

    if deleted:
        runtime_state.drop_stock(normalized_code)
        # Lazy import avoids circular import: tasks -> watchlist_service -> scheduler -> tasks
        from app.engine.scheduler import cancel_snapshot_bootstrap

        cancel_snapshot_bootstrap(normalized_code)
    return deleted


def update_watchlist_status(stock_code: str, status: str) -> int:
    """Update watchlist status (NORMAL / HALT / DELISTED). Returns affected rows."""
    normalized_code = normalize_stock_code(stock_code)
    allowed = {"NORMAL", "HALT", "DELISTED"}
    if status not in allowed:
        raise ValueError(f"invalid status: {status}")

    with get_db() as session:
        updated = (
            session.query(Watchlist)
            .filter(Watchlist.stock_code == normalized_code)
            .update({"status": status})
        )
        session.commit()
    return updated


def restore_halted_to_normal(stock_codes: list[str]) -> int:
    """Bulk restore HALT -> NORMAL for the given codes. Returns affected rows."""
    if not stock_codes:
        return 0
    with get_db() as session:
        updated = (
            session.query(Watchlist)
            .filter(
                Watchlist.status == "HALT",
                Watchlist.stock_code.in_(stock_codes),
            )
            .update({"status": "NORMAL"}, synchronize_session=False)
        )
        session.commit()
    return updated
