"""Risk signal alerts: STOP_LOSS / TAKE_PROFIT / PARTIAL_TP / ADDON."""

from __future__ import annotations

from app.config import settings
from app.db.init_db import init_db
from app.engine.state import runtime_state
from app.services import alert_service
from app.services.alert_service import (
    AlertOutcome,
    format_addon_message,
    format_partial_tp_message,
    format_stop_loss_message,
    format_take_profit_message,
    process_alert,
)
from app.services.wechat_notifier import SendResult


def _setup(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "sqlite_path", str(tmp_path / "amtsm.db"))
    init_db()
    runtime_state.reset_daily()
    runtime_state.exit_fired_today.clear()
    monkeypatch.setattr(
        "app.services.alert_service.is_alert_window_open",
        lambda: True,
    )
    monkeypatch.setattr(
        alert_service.wechat_notifier,
        "send_text",
        lambda _content: SendResult(ok=True, errcode=0, errmsg="ok"),
    )


def test_stop_loss_and_take_profit_templates() -> None:
    stop = format_stop_loss_message(
        stock_name="茅台",
        price=91.0,
        stop_price=92.0,
        avg_cost=100.0,
        pnl_pct=-0.09,
    )
    assert "止损提醒" in stop
    assert "浮亏" in stop

    tp = format_take_profit_message(
        stock_name="茅台",
        price=100.4,
        stop_price=100.5,
        highest_since_hold=120.0,
        pnl_pct=0.004,
    )
    assert "止盈提醒" in tp
    assert "移动止盈线" in tp

    partial = format_partial_tp_message(
        stock_name="茅台", pnl_pct=0.12, suggest_sell_pct=0.30
    )
    assert "阶梯止盈提醒" in partial
    assert "30.00%" in partial

    addon = format_addon_message(
        stock_name="茅台",
        stock_code="sh600519",
        price=10.0,
        actual_n=60,
        low_min=9.5,
        used_coeff=1.1,
    )
    assert "加仓机会" in addon
    assert "买入信号" not in addon


def test_process_stop_loss_once_per_day(tmp_path, monkeypatch) -> None:
    _setup(tmp_path, monkeypatch)
    candidate = {
        "stock_code": "sh600519",
        "stock_name": "茅台",
        "price": 91.0,
        "trade_date": "2026-08-07",
        "baseline_price": 92.0,
        "used_coeff": 1.0,
        "actual_n": 60,
        "stop_price": 92.0,
        "avg_cost": 100.0,
        "pnl_pct": -0.09,
    }
    first = process_alert(candidate, signal_type="STOP_LOSS")
    assert first.outcome == AlertOutcome.SENT
    assert "sh600519" in runtime_state.exit_fired_today

    second = process_alert(candidate, signal_type="STOP_LOSS")
    assert second.outcome in {
        AlertOutcome.SKIPPED_MEMORY,
        AlertOutcome.SKIPPED_ALREADY_SENT,
    }


def test_process_addon_distinct_from_buy(tmp_path, monkeypatch) -> None:
    _setup(tmp_path, monkeypatch)
    candidate = {
        "stock_code": "sh600519",
        "stock_name": "茅台",
        "price": 10.0,
        "trade_date": "2026-08-07",
        "baseline_price": 9.5,
        "used_coeff": 1.1,
        "actual_n": 60,
    }
    result = process_alert(candidate, signal_type="ADDON")
    assert result.outcome == AlertOutcome.SENT
