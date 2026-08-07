from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.db.connection import get_db
from app.db.models import Watchlist
from app.engine.state import runtime_state
from app.schemas.watchlist import WatchlistCreate
from app.services.stock_search_service import normalize_stock_code


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
                    END AS insufficient_days
                FROM watchlist w
                JOIN strategy_config sc ON sc.id = 1
                LEFT JOIN latest_snapshots ls ON ls.stock_code = w.stock_code
                LEFT JOIN latest_baselines lb ON lb.stock_code = w.stock_code
                ORDER BY w.created_at DESC
                LIMIT :limit OFFSET :offset
                """
            ),
            {"limit": limit, "offset": offset},
        ).mappings().all()
    data = [dict(row) for row in rows]
    for item in data:
        item["signal_type"] = runtime_state.signal_state.get(item["stock_code"])
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
        deleted = (
            session.query(Watchlist)
            .filter(Watchlist.stock_code == normalized_code)
            .delete()
        )
        session.commit()
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
