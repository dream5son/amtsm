"""A-share trading-day / session helpers (Asia/Shanghai).

Used by the intraday polling task and by alert DND (do-not-disturb) checks.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, tzinfo
from zoneinfo import ZoneInfo

SH_TZ = ZoneInfo("Asia/Shanghai")
MORNING_START = time(9, 30, 0)
MORNING_END = time(11, 30, 0)
AFTERNOON_START = time(13, 0, 0)
AFTERNOON_END = time(15, 0, 0)


def to_api_iso(
    dt: datetime | None,
    *,
    naive_as: tzinfo = UTC,
) -> str | None:
    """Serialize a datetime for API responses in Asia/Shanghai with offset.

    SQLite ``DateTime`` columns drop tzinfo on round-trip. Call sites that
    persist UTC (``datetime.now(UTC)``, ``CURRENT_TIMESTAMP``) should keep the
    default ``naive_as=UTC``. Aware values are converted regardless.
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=naive_as)
    return dt.astimezone(SH_TZ).isoformat(timespec="seconds")


def is_trading_day(current_date: date) -> bool:
    """Weekday check; holiday calendars are out of scope for V1.0."""
    return current_date.weekday() < 5


def is_in_trading_session(current_time: time) -> bool:
    """True during 09:30-11:30 or 13:00-15:00 (inclusive boundaries)."""
    in_morning = MORNING_START <= current_time <= MORNING_END
    in_afternoon = AFTERNOON_START <= current_time <= AFTERNOON_END
    return in_morning or in_afternoon


def is_alert_window_open(*, now: datetime | None = None) -> bool:
    """Whether alerts may be sent (trading day + trading session).

    Non-trading periods are treated as DND: discard signals, never notify.
    """
    current = now or datetime.now(SH_TZ)
    if current.tzinfo is None:
        current = current.replace(tzinfo=SH_TZ)
    else:
        current = current.astimezone(SH_TZ)
    return is_trading_day(current.date()) and is_in_trading_session(current.time())
