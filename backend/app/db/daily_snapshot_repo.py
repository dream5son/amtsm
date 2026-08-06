from datetime import datetime

from app.db.connection import get_db


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
    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO daily_market_snapshots (
                stock_code, trade_date,
                open_price, high_price, low_price, close_price,
                volume, turnover_rate, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(stock_code, trade_date)
            DO UPDATE SET
                open_price=excluded.open_price,
                high_price=excluded.high_price,
                low_price=excluded.low_price,
                close_price=excluded.close_price,
                volume=excluded.volume,
                turnover_rate=excluded.turnover_rate,
                updated_at=excluded.updated_at
            """,
            (
                stock_code,
                trade_date,
                open_price,
                high_price,
                low_price,
                close_price,
                volume,
                turnover_rate,
                datetime.now().isoformat(),
            ),
        )
        conn.commit()


def get_snapshot(stock_code: str, trade_date: str) -> dict | None:
    with get_db() as conn:
        row = conn.execute(
            """
            SELECT * FROM daily_market_snapshots
            WHERE stock_code=? AND trade_date=?
            """,
            (stock_code, trade_date),
        ).fetchone()
        return dict(row) if row else None
