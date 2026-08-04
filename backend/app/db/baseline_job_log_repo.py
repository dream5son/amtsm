from datetime import datetime

from app.db.connection import get_db


def create_job_log(job_name: str, trade_date: str) -> int:
    with get_db() as conn:
        cursor = conn.execute(
            "INSERT INTO baseline_job_logs (job_name, trade_date, status, started_at) VALUES (?, ?, 'RUNNING', ?)",
            (job_name, trade_date, datetime.now().isoformat()),
        )
        conn.commit()
        return cursor.lastrowid  # type: ignore[return-value]


def finish_job_log(
    job_log_id: int,
    status: str,
    total_count: int,
    success_count: int,
    failed_count: int,
    error_summary: str | None = None,
) -> None:
    with get_db() as conn:
        conn.execute(
            """UPDATE baseline_job_logs
               SET status=?, finished_at=?, total_count=?, success_count=?, failed_count=?, error_summary=?, updated_at=?
               WHERE id=?""",
            (status, datetime.now().isoformat(), total_count, success_count, failed_count, error_summary, datetime.now().isoformat(), job_log_id),
        )
        conn.commit()


def insert_log_item(
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
    with get_db() as conn:
        conn.execute(
            """INSERT INTO baseline_job_log_items
               (job_log_id, stock_code, stock_name, strategy_n, actual_n, status, low_min, high_max, error_message)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (job_log_id, stock_code, stock_name, strategy_n, actual_n, status, low_min, high_max, error_message),
        )
        conn.commit()


def get_latest_job_log() -> dict | None:
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM baseline_job_logs ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return dict(row) if row else None


def get_job_log_by_id(job_log_id: int) -> dict | None:
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM baseline_job_logs WHERE id=?", (job_log_id,)
        ).fetchone()
        return dict(row) if row else None


def get_log_items(job_log_id: int, page: int = 1, size: int = 50) -> list[dict]:
    offset = (page - 1) * size
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM baseline_job_log_items WHERE job_log_id=? ORDER BY id LIMIT ? OFFSET ?",
            (job_log_id, size, offset),
        ).fetchall()
        return [dict(r) for r in rows]
