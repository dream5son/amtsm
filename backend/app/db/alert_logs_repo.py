"""Persistence helpers for alert_logs (buy/sell notification history + freq lock)."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

from app.db.connection import get_db


def get_alert(stock_code: str, trade_date: str, signal_type: str) -> dict | None:
    with get_db() as conn:
        row = conn.execute(
            """
            SELECT *
            FROM alert_logs
            WHERE stock_code = ? AND trade_date = ? AND signal_type = ?
            """,
            (stock_code, trade_date, signal_type),
        ).fetchone()
        return dict(row) if row else None


def insert_alert(
    *,
    stock_code: str,
    trade_date: str,
    signal_type: str,
    trigger_price: float,
    baseline_price: float,
    used_coeff: float,
    sent_status: str,
    error_code: str | None = None,
    error_message: str | None = None,
) -> int | None:
    """Insert an alert log row.

    Returns the new row id, or ``None`` when the unique freq index rejects the insert.
    """
    now = datetime.now(UTC).isoformat()
    try:
        with get_db() as conn:
            cur = conn.execute(
                """
                INSERT INTO alert_logs (
                    stock_code, trade_date, signal_type,
                    trigger_price, baseline_price, used_coeff,
                    sent_status, error_code, error_message, sent_time
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    stock_code,
                    trade_date,
                    signal_type,
                    trigger_price,
                    baseline_price,
                    used_coeff,
                    sent_status,
                    error_code,
                    error_message,
                    now,
                ),
            )
            conn.commit()
            return int(cur.lastrowid)
    except sqlite3.IntegrityError:
        return None


def update_alert_result(
    alert_id: int,
    *,
    trigger_price: float,
    baseline_price: float,
    used_coeff: float,
    sent_status: str,
    error_code: str | None = None,
    error_message: str | None = None,
) -> None:
    """Update an existing alert row after a (re)send attempt."""
    now = datetime.now(UTC).isoformat()
    with get_db() as conn:
        conn.execute(
            """
            UPDATE alert_logs
            SET trigger_price = ?,
                baseline_price = ?,
                used_coeff = ?,
                sent_status = ?,
                error_code = ?,
                error_message = ?,
                sent_time = ?
            WHERE id = ?
            """,
            (
                trigger_price,
                baseline_price,
                used_coeff,
                sent_status,
                error_code,
                error_message,
                now,
                alert_id,
            ),
        )
        conn.commit()
