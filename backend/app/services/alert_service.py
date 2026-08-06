"""Buy-signal alert pipeline (user story 08).

Consumes BUY candidates from the intraday polling task, enforces once-per-day
frequency control, sends WeChat text via ``WeChatNotifier``, and persists
results to ``alert_logs``.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any

from app.config import settings
from app.db import alert_logs_repo
from app.engine.state import runtime_state
from app.services.wechat_notifier import SendResult, wechat_notifier

logger = logging.getLogger(__name__)

SIGNAL_BUY = "BUY"


class AlertOutcome(str, Enum):
    SENT = "sent"
    FAILED = "failed"
    SKIPPED_MEMORY = "skipped_memory"
    SKIPPED_ALREADY_SENT = "skipped_already_sent"
    SKIPPED_RACE = "skipped_race"
    SKIPPED_INVALID = "skipped_invalid"


@dataclass
class AlertProcessResult:
    outcome: AlertOutcome
    stock_code: str
    latency_ms: float | None = None
    error_code: str | None = None
    message: str = ""


def format_buy_message(
    *,
    stock_name: str,
    stock_code: str,
    price: float,
    actual_n: int,
    low_min: float,
    used_coeff: float,
) -> str:
    """PRD buy-signal template (plain text for enterprise WeChat)."""
    return (
        f"【交易助手】发现买入信号！"
        f"您关注的{stock_name}({stock_code})当前价格{_fmt_price(price)}，"
        f"已触及近{actual_n}日低点{_fmt_price(low_min)}的{_fmt_coeff(used_coeff)}倍，"
        f"请及时查看。"
    )


def _fmt_price(value: float) -> str:
    text = f"{value:.4f}".rstrip("0").rstrip(".")
    return text or "0"


def _fmt_coeff(value: float) -> str:
    text = f"{value:.4f}".rstrip("0").rstrip(".")
    return text or "0"


def _freq_key(
    stock_code: str, trade_date: str, signal_type: str
) -> tuple[str, str, str]:
    return (stock_code, trade_date, signal_type)


def _error_fields(result: SendResult) -> tuple[str | None, str | None]:
    code: str | None = None
    if result.errcode is not None:
        code = str(result.errcode)
    elif result.category is not None:
        code = result.category.value
    message = result.message or result.errmsg
    return code, message


def _send_with_retry(content: str) -> tuple[SendResult, float]:
    """Send WeChat text with exponential backoff; returns (result, total_latency_ms)."""
    max_retries = max(0, int(settings.alert_send_max_retries))
    backoff = max(0.0, float(settings.alert_send_retry_backoff_seconds))
    attempts = max_retries + 1
    started = time.perf_counter()
    last: SendResult | None = None

    for attempt in range(attempts):
        attempt_started = time.perf_counter()
        last = wechat_notifier.send_text(content)
        attempt_ms = (time.perf_counter() - attempt_started) * 1000
        if last.ok:
            total_ms = (time.perf_counter() - started) * 1000
            logger.info(
                "buy_alert WeChat send ok attempt=%d/%d latency_ms=%.1f total_ms=%.1f",
                attempt + 1,
                attempts,
                attempt_ms,
                total_ms,
            )
            return last, total_ms

        logger.warning(
            "buy_alert WeChat send failed attempt=%d/%d latency_ms=%.1f category=%s errcode=%s",
            attempt + 1,
            attempts,
            attempt_ms,
            getattr(last.category, "value", None),
            last.errcode,
        )
        if attempt < attempts - 1 and backoff > 0:
            time.sleep(backoff * (2**attempt))

    total_ms = (time.perf_counter() - started) * 1000
    assert last is not None
    return last, total_ms


def _persist_result(
    *,
    existing: dict | None,
    stock_code: str,
    trade_date: str,
    trigger_price: float,
    baseline_price: float,
    used_coeff: float,
    sent_status: str,
    error_code: str | None,
    error_message: str | None,
) -> AlertOutcome | None:
    """Insert or update alert_logs. Returns SKIPPED_RACE when unique insert loses."""
    if existing is not None:
        alert_logs_repo.update_alert_result(
            int(existing["id"]),
            trigger_price=trigger_price,
            baseline_price=baseline_price,
            used_coeff=used_coeff,
            sent_status=sent_status,
            error_code=error_code,
            error_message=error_message,
        )
        return None

    row_id = alert_logs_repo.insert_alert(
        stock_code=stock_code,
        trade_date=trade_date,
        signal_type=SIGNAL_BUY,
        trigger_price=trigger_price,
        baseline_price=baseline_price,
        used_coeff=used_coeff,
        sent_status=sent_status,
        error_code=error_code,
        error_message=error_message,
    )
    if row_id is None:
        logger.info(
            "buy_alert unique constraint race stock=%s date=%s; discarding",
            stock_code,
            trade_date,
        )
        return AlertOutcome.SKIPPED_RACE
    return None


def process_buy_alert(candidate: dict[str, Any]) -> AlertProcessResult:
    """Process a single BUY candidate end-to-end."""
    stock_code = str(candidate.get("stock_code") or "")
    trade_date = str(candidate.get("trade_date") or "")
    stock_name = str(candidate.get("stock_name") or stock_code)

    try:
        price = float(candidate["price"])
        baseline_price = float(candidate["baseline_price"])
        used_coeff = float(candidate["used_coeff"])
        actual_n = int(candidate["actual_n"])
    except (KeyError, TypeError, ValueError) as exc:
        logger.error("buy_alert invalid candidate %s: %s", candidate, exc)
        return AlertProcessResult(
            outcome=AlertOutcome.SKIPPED_INVALID,
            stock_code=stock_code or "unknown",
            message=str(exc),
        )

    if not stock_code or not trade_date:
        return AlertProcessResult(
            outcome=AlertOutcome.SKIPPED_INVALID,
            stock_code=stock_code or "unknown",
            message="missing stock_code or trade_date",
        )

    key = _freq_key(stock_code, trade_date, SIGNAL_BUY)
    if key in runtime_state.sent_signal_keys:
        return AlertProcessResult(
            outcome=AlertOutcome.SKIPPED_MEMORY,
            stock_code=stock_code,
            message="already sent today (memory)",
        )

    existing = alert_logs_repo.get_alert(stock_code, trade_date, SIGNAL_BUY)
    if existing and existing.get("sent_status") == "SUCCESS":
        runtime_state.sent_signal_keys.add(key)
        return AlertProcessResult(
            outcome=AlertOutcome.SKIPPED_ALREADY_SENT,
            stock_code=stock_code,
            message="already sent today (db)",
        )

    content = format_buy_message(
        stock_name=stock_name,
        stock_code=stock_code,
        price=price,
        actual_n=actual_n,
        low_min=baseline_price,
        used_coeff=used_coeff,
    )

    send_result, latency_ms = _send_with_retry(content)
    error_code, error_message = _error_fields(send_result)
    sent_status = "SUCCESS" if send_result.ok else "FAILED"

    race = _persist_result(
        existing=existing,
        stock_code=stock_code,
        trade_date=trade_date,
        trigger_price=price,
        baseline_price=baseline_price,
        used_coeff=used_coeff,
        sent_status=sent_status,
        error_code=error_code,
        error_message=error_message,
    )
    if race is not None:
        return AlertProcessResult(
            outcome=race,
            stock_code=stock_code,
            latency_ms=latency_ms,
            error_code=error_code,
            message="unique constraint race",
        )

    if send_result.ok:
        runtime_state.sent_signal_keys.add(key)
        logger.info(
            "buy_alert SENT stock=%s date=%s price=%s latency_ms=%.1f",
            stock_code,
            trade_date,
            price,
            latency_ms,
        )
        return AlertProcessResult(
            outcome=AlertOutcome.SENT,
            stock_code=stock_code,
            latency_ms=latency_ms,
            message="sent",
        )

    # FAILED: leave memory unset so a later poll can retry.
    logger.error(
        "buy_alert FAILED stock=%s date=%s error_code=%s detail=%s latency_ms=%.1f",
        stock_code,
        trade_date,
        error_code,
        error_message,
        latency_ms,
    )
    return AlertProcessResult(
        outcome=AlertOutcome.FAILED,
        stock_code=stock_code,
        latency_ms=latency_ms,
        error_code=error_code,
        message=error_message or "send failed",
    )


def process_buy_candidates(
    candidates: list[dict[str, Any]],
) -> list[AlertProcessResult]:
    """Handle BUY candidates only; SELL is deferred to user story 09."""
    results: list[AlertProcessResult] = []
    buy_candidates = [c for c in candidates if c.get("signal_type") == SIGNAL_BUY]
    if not buy_candidates:
        return results

    for candidate in buy_candidates:
        try:
            results.append(process_buy_alert(candidate))
        except Exception as exc:
            code = str(candidate.get("stock_code") or "unknown")
            logger.exception("buy_alert unexpected error stock=%s", code)
            results.append(
                AlertProcessResult(
                    outcome=AlertOutcome.FAILED,
                    stock_code=code,
                    message=str(exc),
                )
            )

    sent = sum(1 for r in results if r.outcome == AlertOutcome.SENT)
    failed = sum(1 for r in results if r.outcome == AlertOutcome.FAILED)
    skipped = len(results) - sent - failed
    logger.info(
        "buy_alert batch summary total=%d sent=%d failed=%d skipped=%d",
        len(results),
        sent,
        failed,
        skipped,
    )
    return results
