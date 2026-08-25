"""Exception / resilience helpers for quote delay and structured alerts (US-12)."""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from app.config import settings
from app.engine.market_hours import to_api_iso
from app.engine.state import runtime_state
from app.services.alert_service import get_alert, insert_alert

logger = logging.getLogger(__name__)

SYSTEM_ALERT_STOCK_CODE = "__SYS__"
SYSTEM_ALERT_SIGNAL_TYPE = "DELAY"


def record_poll_round_success() -> None:
    """Clear consecutive failure counter and quote-delay flag after a healthy round."""
    if runtime_state.quote_delay or runtime_state.consecutive_poll_failures:
        logger.info(
            "quote_delay_cleared previous_failures=%d",
            runtime_state.consecutive_poll_failures,
        )
    runtime_state.consecutive_poll_failures = 0
    runtime_state.quote_delay = False
    runtime_state.quote_delay_since = None


def record_poll_round_failure(*, trade_date: str, reason: str) -> None:
    """Increment consecutive poll failures; mark quote delay after threshold."""
    runtime_state.consecutive_poll_failures += 1
    failures = runtime_state.consecutive_poll_failures
    threshold = max(1, int(settings.polling_consecutive_failure_threshold))
    logger.warning(
        "poll_round_failed consecutive=%d threshold=%d trade_date=%s reason=%s",
        failures,
        threshold,
        trade_date,
        reason,
    )
    if failures < threshold:
        return

    newly_marked = not runtime_state.quote_delay
    runtime_state.quote_delay = True
    if newly_marked:
        runtime_state.quote_delay_since = to_api_iso(datetime.now(UTC))
        logger.error(
            "quote_delay_activated consecutive=%d trade_date=%s reason=%s",
            failures,
            trade_date,
            reason,
        )
        _record_internal_delay_alert(trade_date=trade_date, reason=reason)


def _record_internal_delay_alert(*, trade_date: str, reason: str) -> None:
    """Persist a once-per-day system DELAY row into alert_logs for audit."""
    alert_key = f"{trade_date}:{SYSTEM_ALERT_SIGNAL_TYPE}"
    if runtime_state.last_internal_alert_key == alert_key:
        return
    existing = get_alert(SYSTEM_ALERT_STOCK_CODE, trade_date, SYSTEM_ALERT_SIGNAL_TYPE)
    if existing is not None:
        runtime_state.last_internal_alert_key = alert_key
        return

    row_id = insert_alert(
        stock_code=SYSTEM_ALERT_STOCK_CODE,
        trade_date=trade_date,
        signal_type=SYSTEM_ALERT_SIGNAL_TYPE,
        trigger_price=0.0,
        baseline_price=0.0,
        used_coeff=0.0,
        sent_status="INTERNAL",
        error_code="QUOTE_DELAY",
        error_message=reason[:500],
    )
    if row_id is not None:
        runtime_state.last_internal_alert_key = alert_key
        logger.error(
            "internal_alert_recorded type=QUOTE_DELAY trade_date=%s alert_id=%s reason=%s",
            trade_date,
            row_id,
            reason,
        )


def get_system_status() -> dict:
    """Snapshot of resilience flags for API / frontend."""
    return {
        "quote_delay": bool(runtime_state.quote_delay),
        "consecutive_poll_failures": int(runtime_state.consecutive_poll_failures),
        "quote_delay_since": runtime_state.quote_delay_since,
        "failure_threshold": int(settings.polling_consecutive_failure_threshold),
    }
