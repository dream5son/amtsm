"""Tests for user story 10: alert frequency control and DND."""

from __future__ import annotations

import logging
import threading
from datetime import datetime
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

from app.config import settings
from app.db import alert_logs_repo
from app.db.connection import get_db
from app.db.init_db import init_db
from app.engine.market_hours import is_alert_window_open
from app.engine.state import runtime_state
from app.services import alert_service
from app.services.alert_service import (
    AlertOutcome,
    process_buy_alert,
    process_sell_alert,
)
from app.services.wechat_notifier import SendResult


def _candidate(**overrides) -> dict:
    base = {
        "stock_code": "sh600519",
        "stock_name": "贵州茅台",
        "signal_type": "BUY",
        "price": 108.0,
        "trade_date": "2026-08-05",
        "baseline_price": 100.0,
        "used_coeff": 1.10,
        "actual_n": 60,
    }
    base.update(overrides)
    return base


def _ok_send() -> SendResult:
    return SendResult(ok=True, errcode=0, errmsg="ok", message="发送成功")


def test_is_alert_window_open_boundaries() -> None:
    sh = ZoneInfo("Asia/Shanghai")
    assert is_alert_window_open(
        now=datetime(2026, 8, 5, 9, 30, 0, tzinfo=sh)
    )
    assert is_alert_window_open(
        now=datetime(2026, 8, 5, 10, 0, 0, tzinfo=sh)
    )
    assert is_alert_window_open(
        now=datetime(2026, 8, 5, 14, 59, 0, tzinfo=sh)
    )
    assert not is_alert_window_open(
        now=datetime(2026, 8, 5, 11, 45, 0, tzinfo=sh)
    )  # lunch
    assert not is_alert_window_open(
        now=datetime(2026, 8, 5, 15, 1, 0, tzinfo=sh)
    )
    assert not is_alert_window_open(
        now=datetime(2026, 8, 8, 10, 0, 0, tzinfo=sh)
    )  # Saturday


def test_first_buy_same_day_allowed(tmp_path, monkeypatch) -> None:
    """Given first BUY of the day, when freq check runs, Then allow send."""
    sqlite_path = tmp_path / "amtsm.db"
    monkeypatch.setattr(settings, "sqlite_path", str(sqlite_path))
    monkeypatch.setattr(settings, "alert_send_max_retries", 0)
    monkeypatch.setattr(
        "app.services.alert_service.is_alert_window_open",
        lambda **_kwargs: True,
    )
    init_db()
    runtime_state.reset_daily()

    mock_send = MagicMock(return_value=_ok_send())
    monkeypatch.setattr(alert_service.wechat_notifier, "send_text", mock_send)

    result = process_buy_alert(_candidate())
    assert result.outcome == AlertOutcome.SENT
    mock_send.assert_called_once()
    assert ("sh600519", "2026-08-05", "BUY") in runtime_state.sent_signal_keys


def test_second_buy_same_day_blocked(tmp_path, monkeypatch) -> None:
    """Given BUY already sent today, when triggered again, Then block send."""
    sqlite_path = tmp_path / "amtsm.db"
    monkeypatch.setattr(settings, "sqlite_path", str(sqlite_path))
    monkeypatch.setattr(settings, "alert_send_max_retries", 0)
    monkeypatch.setattr(
        "app.services.alert_service.is_alert_window_open",
        lambda **_kwargs: True,
    )
    init_db()
    runtime_state.reset_daily()

    mock_send = MagicMock(return_value=_ok_send())
    monkeypatch.setattr(alert_service.wechat_notifier, "send_text", mock_send)

    first = process_buy_alert(_candidate())
    second = process_buy_alert(_candidate(price=107.0))
    assert first.outcome == AlertOutcome.SENT
    assert second.outcome == AlertOutcome.SKIPPED_MEMORY
    assert mock_send.call_count == 1

    with get_db() as conn:
        count = conn.execute("SELECT COUNT(*) AS c FROM alert_logs").fetchone()["c"]
    assert count == 1


def test_second_buy_blocked_via_db_after_memory_clear(tmp_path, monkeypatch) -> None:
    """Memory miss + SUCCESS in DB still blocks (process restart scenario)."""
    sqlite_path = tmp_path / "amtsm.db"
    monkeypatch.setattr(settings, "sqlite_path", str(sqlite_path))
    monkeypatch.setattr(settings, "alert_send_max_retries", 0)
    monkeypatch.setattr(
        "app.services.alert_service.is_alert_window_open",
        lambda **_kwargs: True,
    )
    init_db()
    runtime_state.reset_daily()

    mock_send = MagicMock(return_value=_ok_send())
    monkeypatch.setattr(alert_service.wechat_notifier, "send_text", mock_send)

    assert process_buy_alert(_candidate()).outcome == AlertOutcome.SENT
    runtime_state.sent_signal_keys.clear()

    second = process_buy_alert(_candidate(price=106.0))
    assert second.outcome == AlertOutcome.SKIPPED_ALREADY_SENT
    assert mock_send.call_count == 1
    assert ("sh600519", "2026-08-05", "BUY") in runtime_state.sent_signal_keys


def test_dnd_outside_trading_hours_discards(tmp_path, monkeypatch) -> None:
    """Given non-trading hours, when threshold hits, Then send nothing."""
    sqlite_path = tmp_path / "amtsm.db"
    monkeypatch.setattr(settings, "sqlite_path", str(sqlite_path))
    monkeypatch.setattr(settings, "alert_send_max_retries", 0)
    monkeypatch.setattr(
        "app.services.alert_service.is_alert_window_open",
        lambda **_kwargs: False,
    )
    init_db()
    runtime_state.reset_daily()

    mock_send = MagicMock(return_value=_ok_send())
    monkeypatch.setattr(alert_service.wechat_notifier, "send_text", mock_send)

    result = process_buy_alert(_candidate())
    assert result.outcome == AlertOutcome.SKIPPED_DND
    mock_send.assert_not_called()

    with get_db() as conn:
        count = conn.execute("SELECT COUNT(*) AS c FROM alert_logs").fetchone()["c"]
    assert count == 0


def test_unique_constraint_race_only_one_sends(tmp_path, monkeypatch, caplog) -> None:
    """Given concurrent claims, When unique index conflicts, Then only one sends."""
    sqlite_path = tmp_path / "amtsm.db"
    monkeypatch.setattr(settings, "sqlite_path", str(sqlite_path))
    monkeypatch.setattr(settings, "alert_send_max_retries", 0)
    monkeypatch.setattr(
        "app.services.alert_service.is_alert_window_open",
        lambda **_kwargs: True,
    )
    init_db()
    runtime_state.reset_daily()

    barrier = threading.Barrier(2)
    results: list[AlertOutcome] = []
    lock = threading.Lock()
    send_count = {"n": 0}

    def _slow_send(content: str) -> SendResult:
        send_count["n"] += 1
        return _ok_send()

    original_insert = alert_logs_repo.insert_alert

    def _insert_after_barrier(**kwargs):
        barrier.wait(timeout=5)
        return original_insert(**kwargs)

    monkeypatch.setattr(alert_logs_repo, "insert_alert", _insert_after_barrier)
    monkeypatch.setattr(alert_service.wechat_notifier, "send_text", _slow_send)

    def _worker() -> None:
        outcome = process_buy_alert(_candidate()).outcome
        with lock:
            results.append(outcome)

    threads = [threading.Thread(target=_worker) for _ in range(2)]
    with caplog.at_level(logging.INFO, logger="app.services.alert_service"):
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

    assert len(results) == 2
    assert AlertOutcome.SENT in results
    assert AlertOutcome.SKIPPED_RACE in results
    assert send_count["n"] == 1

    with get_db() as conn:
        count = conn.execute("SELECT COUNT(*) AS c FROM alert_logs").fetchone()["c"]
    assert count == 1
    assert any("unique constraint race" in rec.message for rec in caplog.records)


def test_buy_and_sell_independent_freq_keys(tmp_path, monkeypatch) -> None:
    """BUY and SELL share stock+date but not signal_type — both may send once."""
    sqlite_path = tmp_path / "amtsm.db"
    monkeypatch.setattr(settings, "sqlite_path", str(sqlite_path))
    monkeypatch.setattr(settings, "alert_send_max_retries", 0)
    monkeypatch.setattr(
        "app.services.alert_service.is_alert_window_open",
        lambda **_kwargs: True,
    )
    init_db()
    runtime_state.reset_daily()

    mock_send = MagicMock(return_value=_ok_send())
    monkeypatch.setattr(alert_service.wechat_notifier, "send_text", mock_send)

    buy = process_buy_alert(_candidate())
    sell = process_sell_alert(
        _candidate(
            signal_type="SELL",
            price=117.0,
            baseline_price=130.0,
            used_coeff=0.90,
        )
    )
    assert buy.outcome == AlertOutcome.SENT
    assert sell.outcome == AlertOutcome.SENT
    assert mock_send.call_count == 2
    assert ("sh600519", "2026-08-05", "BUY") in runtime_state.sent_signal_keys
    assert ("sh600519", "2026-08-05", "SELL") in runtime_state.sent_signal_keys
