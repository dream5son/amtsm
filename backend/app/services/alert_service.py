"""Signal alert pipeline (BUY/SELL + V2 risk signals).

Consumes candidates from the intraday polling task, enforces
trading-hours DND + once-per-day frequency control (memory Set + unique
index claim), sends WeChat text via ``WeChatNotifier``, and persists
results to ``alert_logs``.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.config import settings
from app.db.connection import get_db
from app.db.models import AlertLog
from app.engine.market_hours import is_alert_window_open
from app.engine.state import runtime_state
from app.services.wechat_notifier import SendResult, wechat_notifier

logger = logging.getLogger(__name__)

SIGNAL_BUY = "BUY"
SIGNAL_SELL = "SELL"
SIGNAL_STOP_LOSS = "STOP_LOSS"
SIGNAL_TAKE_PROFIT = "TAKE_PROFIT"
SIGNAL_PARTIAL_TP = "PARTIAL_TP"
SIGNAL_ADDON = "ADDON"

EXIT_SIGNAL_TYPES = frozenset({SIGNAL_STOP_LOSS, SIGNAL_TAKE_PROFIT})
RISK_SIGNAL_TYPES = frozenset(
    {SIGNAL_STOP_LOSS, SIGNAL_TAKE_PROFIT, SIGNAL_PARTIAL_TP, SIGNAL_ADDON}
)

STATUS_PENDING = "PENDING"
STATUS_SUCCESS = "SUCCESS"
STATUS_FAILED = "FAILED"

LIMIT_BOARD_NOTE = "（注：该股当前可能处于涨跌停状态，请注意流动性风险）"
T1_NOTE = "（注：当日买入部分暂受T+1限制，无法当日卖出）"


def _alert_to_dict(row: AlertLog) -> dict:
    return {
        "id": row.id,
        "stock_code": row.stock_code,
        "trade_date": row.trade_date,
        "signal_type": row.signal_type,
        "trigger_price": row.trigger_price,
        "baseline_price": row.baseline_price,
        "used_coeff": row.used_coeff,
        "sent_status": row.sent_status,
        "error_code": row.error_code,
        "error_message": row.error_message,
        "sent_time": row.sent_time,
    }


def get_alert(stock_code: str, trade_date: str, signal_type: str) -> dict | None:
    with get_db() as session:
        row = session.scalars(
            select(AlertLog).where(
                AlertLog.stock_code == stock_code,
                AlertLog.trade_date == trade_date,
                AlertLog.signal_type == signal_type,
            )
        ).first()
        return _alert_to_dict(row) if row else None


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
    now = datetime.now(UTC)
    try:
        with get_db() as session:
            row = AlertLog(
                stock_code=stock_code,
                trade_date=trade_date,
                signal_type=signal_type,
                trigger_price=trigger_price,
                baseline_price=baseline_price,
                used_coeff=used_coeff,
                sent_status=sent_status,
                error_code=error_code,
                error_message=error_message,
                sent_time=now,
            )
            session.add(row)
            session.commit()
            return int(row.id)
    except IntegrityError:
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
    now = datetime.now(UTC)
    with get_db() as session:
        row = session.get(AlertLog, alert_id)
        if row is None:
            return
        row.trigger_price = trigger_price
        row.baseline_price = baseline_price
        row.used_coeff = used_coeff
        row.sent_status = sent_status
        row.error_code = error_code
        row.error_message = error_message
        row.sent_time = now
        session.commit()


class AlertOutcome(str, Enum):
    SENT = "sent"
    FAILED = "failed"
    SKIPPED_DND = "skipped_dnd"
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


def format_sell_message(
    *,
    stock_name: str,
    stock_code: str,
    price: float,
    actual_n: int,
    high_max: float,
    used_coeff: float,
    is_limit_up: bool = False,
) -> str:
    """PRD sell-signal template (plain text for enterprise WeChat)."""
    text = (
        f"【交易助手】发现卖出信号！"
        f"您关注的{stock_name}({stock_code})当前价格{_fmt_price(price)}，"
        f"已触及近{actual_n}日高点{_fmt_price(high_max)}的{_fmt_coeff(used_coeff)}倍，"
        f"请考虑减仓。"
    )
    if is_limit_up:
        text += LIMIT_BOARD_NOTE
    return text


def format_stop_loss_message(
    *,
    stock_name: str,
    price: float,
    stop_price: float,
    avg_cost: float,
    pnl_pct: float,
    t1_note: bool = False,
) -> str:
    text = (
        f"【交易助手】止损提醒！"
        f"您持有的{stock_name}当前价格{_fmt_price(price)}，"
        f"已跌破止损线{_fmt_price(stop_price)}"
        f"（成本价{_fmt_price(avg_cost)}，浮亏{_fmt_pct(pnl_pct)}），"
        f"建议及时止损离场，控制风险。"
    )
    if t1_note:
        text += T1_NOTE
    return text


def format_take_profit_message(
    *,
    stock_name: str,
    price: float,
    stop_price: float,
    highest_since_hold: float,
    pnl_pct: float,
    t1_note: bool = False,
) -> str:
    text = (
        f"【交易助手】止盈提醒！"
        f"您持有的{stock_name}当前价格{_fmt_price(price)}，"
        f"已触及移动止盈线{_fmt_price(stop_price)}"
        f"（持仓期间最高价{_fmt_price(highest_since_hold)}，当前浮盈{_fmt_pct(pnl_pct)}），"
        f"建议及时锁定利润，可考虑减仓或清仓。"
    )
    if t1_note:
        text += T1_NOTE
    return text


def format_partial_tp_message(
    *,
    stock_name: str,
    pnl_pct: float,
    suggest_sell_pct: float,
) -> str:
    return (
        f"【交易助手】阶梯止盈提醒！"
        f"您持有的{stock_name}浮盈已达{_fmt_pct(pnl_pct)}，"
        f"建议减持{_fmt_pct(suggest_sell_pct)}仓位锁定部分利润，"
        f"剩余仓位继续持有博取更大收益。"
    )


def format_addon_message(
    *,
    stock_name: str,
    stock_code: str,
    price: float,
    actual_n: int,
    low_min: float,
    used_coeff: float,
) -> str:
    return (
        f"【交易助手】发现加仓机会！"
        f"您持有的{stock_name}({stock_code})当前价格{_fmt_price(price)}，"
        f"已触及近{actual_n}日低点{_fmt_price(low_min)}的{_fmt_coeff(used_coeff)}倍，"
        f"可考虑逢低加仓（与空仓买入提醒区分）。"
    )


def limit_up_ratio(stock_code: str, stock_name: str = "") -> float:
    """Return the daily limit-up multiplier for an A-share code."""
    name = (stock_name or "").upper()
    if "ST" in name:
        return 1.05

    code = stock_code.lower()
    numeric = code[2:] if len(code) >= 8 and code[:2] in {"sh", "sz", "bj"} else code

    if code.startswith("bj") or numeric.startswith(("43", "83", "87", "88")):
        return 1.30
    if numeric.startswith(("30", "68")):
        return 1.20
    return 1.10


def is_limit_up(
    *,
    stock_code: str,
    price: float,
    prev_close: float | None,
    stock_name: str = "",
) -> bool:
    """Heuristic limit-up check using prev_close and board-specific ratio."""
    if prev_close is None or prev_close <= 0 or price <= 0:
        return False
    limit_price = round(prev_close * limit_up_ratio(stock_code, stock_name), 2)
    return price >= limit_price


def _fmt_price(value: float) -> str:
    text = f"{value:.4f}".rstrip("0").rstrip(".")
    return text or "0"


def _fmt_coeff(value: float) -> str:
    text = f"{value:.4f}".rstrip("0").rstrip(".")
    return text or "0"


def _fmt_pct(value: float) -> str:
    return f"{value * 100:.2f}%"


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


def _send_with_retry(content: str, *, log_prefix: str) -> tuple[SendResult, float]:
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
                "%s WeChat send ok attempt=%d/%d latency_ms=%.1f total_ms=%.1f",
                log_prefix,
                attempt + 1,
                attempts,
                attempt_ms,
                total_ms,
            )
            return last, total_ms

        logger.warning(
            "%s WeChat send failed attempt=%d/%d latency_ms=%.1f category=%s errcode=%s",
            log_prefix,
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


def _claim_alert_slot(
    *,
    stock_code: str,
    trade_date: str,
    signal_type: str,
    trigger_price: float,
    baseline_price: float,
    used_coeff: float,
    log_prefix: str,
) -> tuple[dict | None, AlertOutcome | None]:
    """Claim the once-per-day alert slot before sending.

    Returns ``(row, None)`` when this caller owns the slot (new PENDING claim or
    an existing FAILED/PENDING row eligible for retry). Returns
    ``(None, SKIPPED_*)`` when the signal must be discarded.
    """
    existing = get_alert(stock_code, trade_date, signal_type)
    if existing is not None:
        status = existing.get("sent_status")
        if status == STATUS_SUCCESS:
            runtime_state.sent_signal_keys.add(
                _freq_key(stock_code, trade_date, signal_type)
            )
            return None, AlertOutcome.SKIPPED_ALREADY_SENT
        return existing, None

    row_id = insert_alert(
        stock_code=stock_code,
        trade_date=trade_date,
        signal_type=signal_type,
        trigger_price=trigger_price,
        baseline_price=baseline_price,
        used_coeff=used_coeff,
        sent_status=STATUS_PENDING,
    )
    if row_id is None:
        logger.info(
            "%s unique constraint race stock=%s date=%s type=%s; discarding",
            log_prefix,
            stock_code,
            trade_date,
            signal_type,
        )
        return None, AlertOutcome.SKIPPED_RACE

    return (
        {
            "id": row_id,
            "stock_code": stock_code,
            "trade_date": trade_date,
            "signal_type": signal_type,
            "sent_status": STATUS_PENDING,
        },
        None,
    )


def _build_message(
    signal_type: str, candidate: dict[str, Any], parsed: dict[str, Any]
) -> str:
    t1_note = bool(candidate.get("t1_note"))

    if signal_type == SIGNAL_BUY:
        return format_buy_message(
            stock_name=parsed["stock_name"],
            stock_code=parsed["stock_code"],
            price=parsed["price"],
            actual_n=parsed["actual_n"],
            low_min=parsed["baseline_price"],
            used_coeff=parsed["used_coeff"],
        )

    if signal_type == SIGNAL_ADDON:
        return format_addon_message(
            stock_name=parsed["stock_name"],
            stock_code=parsed["stock_code"],
            price=parsed["price"],
            actual_n=parsed["actual_n"],
            low_min=parsed["baseline_price"],
            used_coeff=parsed["used_coeff"],
        )

    if signal_type == SIGNAL_STOP_LOSS:
        return format_stop_loss_message(
            stock_name=parsed["stock_name"],
            price=parsed["price"],
            stop_price=float(candidate.get("stop_price") or parsed["baseline_price"]),
            avg_cost=float(candidate.get("avg_cost") or 0),
            pnl_pct=float(candidate.get("pnl_pct") or 0),
            t1_note=t1_note,
        )

    if signal_type == SIGNAL_TAKE_PROFIT:
        return format_take_profit_message(
            stock_name=parsed["stock_name"],
            price=parsed["price"],
            stop_price=float(candidate.get("stop_price") or parsed["baseline_price"]),
            highest_since_hold=float(
                candidate.get("highest_since_hold") or parsed["baseline_price"]
            ),
            pnl_pct=float(candidate.get("pnl_pct") or 0),
            t1_note=t1_note,
        )

    if signal_type == SIGNAL_PARTIAL_TP:
        return format_partial_tp_message(
            stock_name=parsed["stock_name"],
            pnl_pct=float(candidate.get("pnl_pct") or 0),
            suggest_sell_pct=float(
                candidate.get("suggest_sell_pct") or parsed["used_coeff"]
            ),
        )

    limit_flag = candidate.get("is_limit_up")
    if limit_flag is None:
        limit_flag = is_limit_up(
            stock_code=parsed["stock_code"],
            price=parsed["price"],
            prev_close=_optional_float(candidate.get("prev_close")),
            stock_name=parsed["stock_name"],
        )
    return format_sell_message(
        stock_name=parsed["stock_name"],
        stock_code=parsed["stock_code"],
        price=parsed["price"],
        actual_n=parsed["actual_n"],
        high_max=parsed["baseline_price"],
        used_coeff=parsed["used_coeff"],
        is_limit_up=bool(limit_flag),
    )


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_candidate(
    candidate: dict[str, Any],
) -> tuple[dict[str, Any] | None, AlertProcessResult | None]:
    stock_code = str(candidate.get("stock_code") or "")
    trade_date = str(candidate.get("trade_date") or "")
    stock_name = str(candidate.get("stock_name") or stock_code)

    try:
        price = float(candidate["price"])
        baseline_price = float(candidate["baseline_price"])
        used_coeff = float(candidate["used_coeff"])
        actual_n = int(candidate.get("actual_n") or 0)
    except (KeyError, TypeError, ValueError) as exc:
        return None, AlertProcessResult(
            outcome=AlertOutcome.SKIPPED_INVALID,
            stock_code=stock_code or "unknown",
            message=str(exc),
        )

    if not stock_code or not trade_date:
        return None, AlertProcessResult(
            outcome=AlertOutcome.SKIPPED_INVALID,
            stock_code=stock_code or "unknown",
            message="missing stock_code or trade_date",
        )

    return (
        {
            "stock_code": stock_code,
            "trade_date": trade_date,
            "stock_name": stock_name,
            "price": price,
            "baseline_price": baseline_price,
            "used_coeff": used_coeff,
            "actual_n": actual_n,
        },
        None,
    )


def _log_prefix_for(signal_type: str) -> str:
    mapping = {
        SIGNAL_BUY: "buy_alert",
        SIGNAL_SELL: "sell_alert",
        SIGNAL_STOP_LOSS: "stop_loss_alert",
        SIGNAL_TAKE_PROFIT: "take_profit_alert",
        SIGNAL_PARTIAL_TP: "partial_tp_alert",
        SIGNAL_ADDON: "addon_alert",
    }
    return mapping.get(signal_type, f"{signal_type.lower()}_alert")


def process_alert(candidate: dict[str, Any], *, signal_type: str) -> AlertProcessResult:
    """Process a single signal candidate end-to-end."""
    log_prefix = _log_prefix_for(signal_type)
    parsed, invalid = _parse_candidate(candidate)
    if invalid is not None:
        logger.error(
            "%s invalid candidate %s: %s", log_prefix, candidate, invalid.message
        )
        return invalid
    assert parsed is not None

    stock_code = parsed["stock_code"]
    trade_date = parsed["trade_date"]
    price = parsed["price"]
    baseline_price = parsed["baseline_price"]
    used_coeff = parsed["used_coeff"]

    if not is_alert_window_open():
        logger.info(
            "%s DND discard stock=%s date=%s type=%s (outside trading hours)",
            log_prefix,
            stock_code,
            trade_date,
            signal_type,
        )
        return AlertProcessResult(
            outcome=AlertOutcome.SKIPPED_DND,
            stock_code=stock_code,
            message="outside trading hours (DND)",
        )

    key = _freq_key(stock_code, trade_date, signal_type)
    if key in runtime_state.sent_signal_keys:
        return AlertProcessResult(
            outcome=AlertOutcome.SKIPPED_MEMORY,
            stock_code=stock_code,
            message="already sent today (memory)",
        )

    claimed, skip = _claim_alert_slot(
        stock_code=stock_code,
        trade_date=trade_date,
        signal_type=signal_type,
        trigger_price=price,
        baseline_price=baseline_price,
        used_coeff=used_coeff,
        log_prefix=log_prefix,
    )
    if skip is not None:
        return AlertProcessResult(
            outcome=skip,
            stock_code=stock_code,
            message=(
                "already sent today (db)"
                if skip == AlertOutcome.SKIPPED_ALREADY_SENT
                else "unique constraint race"
            ),
        )
    assert claimed is not None

    content = _build_message(signal_type, candidate, parsed)
    send_result, latency_ms = _send_with_retry(content, log_prefix=log_prefix)
    error_code, error_message = _error_fields(send_result)
    sent_status = STATUS_SUCCESS if send_result.ok else STATUS_FAILED

    update_alert_result(
        int(claimed["id"]),
        trigger_price=price,
        baseline_price=baseline_price,
        used_coeff=used_coeff,
        sent_status=sent_status,
        error_code=error_code,
        error_message=error_message,
    )

    if send_result.ok:
        runtime_state.sent_signal_keys.add(key)
        if signal_type in EXIT_SIGNAL_TYPES:
            runtime_state.exit_fired_today.add(stock_code)
        logger.info(
            "%s SENT stock=%s date=%s price=%s latency_ms=%.1f",
            log_prefix,
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

    logger.error(
        "%s FAILED stock=%s date=%s error_code=%s detail=%s latency_ms=%.1f",
        log_prefix,
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


def process_buy_alert(candidate: dict[str, Any]) -> AlertProcessResult:
    """Process a single BUY candidate end-to-end."""
    return process_alert(candidate, signal_type=SIGNAL_BUY)


def process_sell_alert(candidate: dict[str, Any]) -> AlertProcessResult:
    """Process a single SELL candidate end-to-end."""
    return process_alert(candidate, signal_type=SIGNAL_SELL)


def _process_candidates(
    candidates: list[dict[str, Any]],
    *,
    signal_type: str,
) -> list[AlertProcessResult]:
    results: list[AlertProcessResult] = []
    filtered = [c for c in candidates if c.get("signal_type") == signal_type]
    if not filtered:
        return results

    log_prefix = _log_prefix_for(signal_type)
    for candidate in filtered:
        try:
            results.append(process_alert(candidate, signal_type=signal_type))
        except Exception as exc:
            code = str(candidate.get("stock_code") or "unknown")
            logger.exception("%s unexpected error stock=%s", log_prefix, code)
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
        "%s batch summary total=%d sent=%d failed=%d skipped=%d",
        log_prefix,
        len(results),
        sent,
        failed,
        skipped,
    )
    return results


def process_buy_candidates(
    candidates: list[dict[str, Any]],
) -> list[AlertProcessResult]:
    """Handle BUY candidates; failures never block the rest of the batch."""
    return _process_candidates(candidates, signal_type=SIGNAL_BUY)


def process_sell_candidates(
    candidates: list[dict[str, Any]],
) -> list[AlertProcessResult]:
    """Handle SELL candidates; failures never block the rest of the batch."""
    return _process_candidates(candidates, signal_type=SIGNAL_SELL)


def process_risk_candidates(
    candidates: list[dict[str, Any]],
) -> list[AlertProcessResult]:
    """Handle STOP_LOSS / TAKE_PROFIT / PARTIAL_TP / ADDON candidates."""
    results: list[AlertProcessResult] = []
    for signal_type in (
        SIGNAL_STOP_LOSS,
        SIGNAL_TAKE_PROFIT,
        SIGNAL_PARTIAL_TP,
        SIGNAL_ADDON,
    ):
        results.extend(_process_candidates(candidates, signal_type=signal_type))
    return results
