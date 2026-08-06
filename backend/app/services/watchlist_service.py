import sqlite3

from app.db.connection import get_db
from app.engine.state import runtime_state
from app.schemas.watchlist import WatchlistCreate
from app.services.stock_search_service import normalize_stock_code


def list_watchlist(limit: int = 50, offset: int = 0) -> list[dict]:
    if limit < 1 or limit > 200:
        raise ValueError("limit out of range")
    if offset < 0:
        raise ValueError("offset must be >= 0")

    with get_db() as conn:
        rows = conn.execute(
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
                END AS insufficient_days
            FROM watchlist w
            JOIN strategy_config sc ON sc.id = 1
            LEFT JOIN latest_snapshots ls ON ls.stock_code = w.stock_code
            LEFT JOIN latest_baselines lb ON lb.stock_code = w.stock_code
            ORDER BY w.created_at DESC
            LIMIT ? OFFSET ?
            """
            ,
            (limit, offset),
        ).fetchall()
    data = [dict(row) for row in rows]
    for item in data:
        item["signal_type"] = runtime_state.signal_state.get(item["stock_code"])
    return data


def add_watchlist(payload: WatchlistCreate) -> None:
    normalized_code = normalize_stock_code(payload.stock_code)

    with get_db() as conn:
        try:
            conn.execute(
                "INSERT INTO watchlist (stock_code, stock_name) VALUES (?, ?)",
                (normalized_code, payload.stock_name),
            )
            conn.commit()
        except sqlite3.IntegrityError as exc:
            raise ValueError("stock already exists") from exc


def remove_watchlist(stock_code: str) -> int:
    normalized_code = normalize_stock_code(stock_code)

    with get_db() as conn:
        cursor = conn.execute("DELETE FROM watchlist WHERE stock_code = ?", (normalized_code,))
        conn.commit()
    return cursor.rowcount
