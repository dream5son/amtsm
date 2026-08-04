from datetime import datetime

from app.db.connection import get_db


def upsert_baseline(stock_code: str, trade_date: str, low_min: float, high_max: float, actual_n: int) -> None:
    with get_db() as conn:
        conn.execute(
            """INSERT INTO daily_baselines (stock_code, trade_date, low_min, high_max, actual_n, updated_at)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(stock_code, trade_date)
               DO UPDATE SET low_min=excluded.low_min, high_max=excluded.high_max, actual_n=excluded.actual_n, updated_at=excluded.updated_at""",
            (stock_code, trade_date, low_min, high_max, actual_n, datetime.now().isoformat()),
        )
        conn.commit()


def get_baselines_by_date(trade_date: str) -> list[dict]:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM daily_baselines WHERE trade_date=?", (trade_date,)
        ).fetchall()
        return [dict(r) for r in rows]
