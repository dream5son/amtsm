"""In-memory scheduler activity log, bucketed by job type for the homepage panel."""

from __future__ import annotations

import functools
import logging
import threading
from collections import deque
from collections.abc import Callable
from contextvars import ContextVar
from datetime import datetime
from typing import Any

from app.engine.market_hours import SH_TZ

JOB_IDS: tuple[str, ...] = (
    "market_polling",
    "baseline_precompute",
    "daily_snapshot",
    "backtest_worker",
    "startup_market_data_catchup",
    "snapshot_bootstrap",
)

LEVELS: tuple[str, ...] = ("info", "error", "notify")

BUFFER_MAXLEN = 100
DEFAULT_LIMIT = 20

_LOCK = threading.Lock()
_BUFFERS: dict[str, deque[dict[str, Any]]] = {
    job: deque(maxlen=BUFFER_MAXLEN) for job in JOB_IDS
}
_NEXT_ID = 1
_CURRENT_JOB: ContextVar[str | None] = ContextVar("activity_job", default=None)

_LOGGER_DEFAULT_JOB: dict[str, str] = {
    "app.engine.backtest_tasks": "backtest_worker",
    "app.services.backtest_service": "backtest_worker",
    "app.services.alert_service": "market_polling",
}

# Longer / more specific prefixes first so catch-up logs are not filed as baseline.
_MESSAGE_HINTS: tuple[tuple[str, str], ...] = (
    ("startup_market_data_catchup", "startup_market_data_catchup"),
    ("bootstrap_watchlist_snapshot", "snapshot_bootstrap"),
    ("backtest_worker", "backtest_worker"),
    ("intraday_snapshot", "market_polling"),
    ("daily_snapshot", "daily_snapshot"),
    ("market_polling", "market_polling"),
    ("baseline_precompute", "baseline_precompute"),
    ("baseline_degraded", "baseline_precompute"),
    ("baseline_market_data", "baseline_precompute"),
    ("baseline_stock_failed", "baseline_precompute"),
    ("baseline_unexpected", "baseline_precompute"),
    ("halt_resume", "baseline_precompute"),
    ("halt_status_restored", "baseline_precompute"),
)

_WATCHED_LOGGERS: tuple[str, ...] = (
    "app.engine.tasks",
    "app.engine.backtest_tasks",
    "app.services.backtest_service",
    "app.services.alert_service",
)


def map_scheduler_job_id(job_id: str) -> str | None:
    """Map an APScheduler job id (including dynamic bootstrap ids) to a bucket."""
    if job_id in JOB_IDS:
        return job_id
    if job_id.startswith("snapshot_bootstrap"):
        return "snapshot_bootstrap"
    return None


def resolve_job(
    *,
    job_id: str | None = None,
    logger_name: str = "",
    message: str = "",
) -> str | None:
    current = _CURRENT_JOB.get()
    if current in JOB_IDS:
        return current
    if job_id:
        mapped = map_scheduler_job_id(job_id)
        if mapped is not None:
            return mapped
    for prefix, job in _MESSAGE_HINTS:
        if prefix in message:
            return job
    if logger_name in _LOGGER_DEFAULT_JOB:
        return _LOGGER_DEFAULT_JOB[logger_name]
    for name, job in _LOGGER_DEFAULT_JOB.items():
        if logger_name.startswith(name):
            return job
    return None


def classify_level(levelno: int, message: str, *, logger_name: str = "") -> str:
    if " SENT " in message and (
        logger_name.startswith("app.services.alert_service") or "alert" in message
    ):
        return "notify"
    if levelno >= logging.ERROR:
        return "error"
    return "info"


def emit(level: str, message: str, *, job: str | None = None) -> dict[str, Any] | None:
    """Append one activity entry. Returns the stored dict, or None if job is unknown."""
    global _NEXT_ID
    resolved = job if job in JOB_IDS else resolve_job(message=message)
    if resolved is None:
        return None
    if level not in LEVELS:
        level = "info"
    with _LOCK:
        entry = {
            "id": _NEXT_ID,
            "ts": datetime.now(SH_TZ).isoformat(timespec="seconds"),
            "level": level,
            "job": resolved,
            "message": message,
        }
        _NEXT_ID += 1
        _BUFFERS[resolved].append(entry)
        return dict(entry)


def recent(job: str, limit: int = DEFAULT_LIMIT) -> list[dict[str, Any]]:
    if job not in JOB_IDS:
        return []
    size = max(1, min(int(limit), BUFFER_MAXLEN))
    with _LOCK:
        items = list(_BUFFERS[job])
    return items[-size:]


def snapshot(limit: int = DEFAULT_LIMIT) -> dict[str, list[dict[str, Any]]]:
    return {job: recent(job, limit) for job in JOB_IDS}


def since(after_id: int) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    with _LOCK:
        for buf in _BUFFERS.values():
            items.extend(entry for entry in buf if entry["id"] > after_id)
    items.sort(key=lambda entry: entry["id"])
    return items


def max_id() -> int:
    with _LOCK:
        return _NEXT_ID - 1


def reset_activity_log() -> None:
    """Clear buffers; used by unit tests."""
    global _NEXT_ID
    with _LOCK:
        for buf in _BUFFERS.values():
            buf.clear()
        _NEXT_ID = 1


def track_job(job: str, func: Callable[..., Any]) -> Callable[..., Any]:
    """Wrap a scheduler callable so its logs are filed under ``job``."""

    @functools.wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        token = _CURRENT_JOB.set(job)
        try:
            return func(*args, **kwargs)
        finally:
            _CURRENT_JOB.reset(token)

    return wrapper


class ActivityLogHandler(logging.Handler):
    """Capture INFO+ records from scheduler task loggers into the activity buffer."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            if record.levelno < logging.INFO:
                return
            message = record.getMessage()
            job = resolve_job(
                logger_name=record.name,
                message=message,
            )
            if job is None:
                return
            level = classify_level(
                record.levelno, message, logger_name=record.name
            )
            emit(level, message, job=job)
        except Exception:  # noqa: BLE001 - logging handlers must not raise
            self.handleError(record)


def install_activity_logging() -> None:
    """Attach the activity handler to watched loggers that do not have one yet."""
    handler: ActivityLogHandler | None = None
    for name in _WATCHED_LOGGERS:
        log = logging.getLogger(name)
        existing = next(
            (item for item in log.handlers if isinstance(item, ActivityLogHandler)),
            None,
        )
        if existing is not None:
            handler = existing
            continue
        if handler is None:
            handler = ActivityLogHandler()
            handler.setLevel(logging.INFO)
        log.setLevel(logging.INFO)
        log.addHandler(handler)
