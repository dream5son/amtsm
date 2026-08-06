"""Tests for user story 08: buy-signal WeChat alerts."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

import pytest

from app.config import settings
from app.db.connection import get_db
from app.db.init_db import init_db
from app.engine.state import runtime_state
from app.engine.tasks import market_polling_task
from app.schemas.watchlist import WatchlistCreate
from app.services import alert_service
from app.services.alert_service import (
    AlertOutcome,
    format_buy_message,
    process_buy_alert,
    process_buy_candidates,
)
from app.services.watchlist_service import add_watchlist
from app.services.wechat_notifier import ErrorCategory, SendResult


@pytest.fixture(autouse=True)
def _open_alert_window(monkeypatch) -> None:
    """US08 focuses on send/content; DND is covered by US10."""
    monkeypatch.setattr(
        "app.services.alert_service.is_alert_window_open",
        lambda **_kwargs: True,
    )


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


def test_format_buy_message_contains_required_fields() -> None:
    text = format_buy_message(
        stock_name="贵州茅台",
        stock_code="sh600519",
        price=108.0,
        actual_n=60,
        low_min=100.0,
        used_coeff=1.1,
    )
    assert "贵州茅台" in text
    assert "sh600519" in text
    assert "108" in text
    assert "60" in text
    assert "100" in text
    assert "1.1" in text
    assert "买入信号" in text


def test_process_buy_alert_sends_and_logs_success(tmp_path, monkeypatch) -> None:
    sqlite_path = tmp_path / "amtsm.db"
    monkeypatch.setattr(settings, "sqlite_path", str(sqlite_path))
    monkeypatch.setattr(settings, "alert_send_max_retries", 0)
    init_db()
    runtime_state.reset_daily()

    mock_send = MagicMock(
        return_value=SendResult(ok=True, errcode=0, errmsg="ok", message="发送成功")
    )
    monkeypatch.setattr(alert_service.wechat_notifier, "send_text", mock_send)

    result = process_buy_alert(_candidate())
    assert result.outcome == AlertOutcome.SENT
    mock_send.assert_called_once()
    content = mock_send.call_args.args[0]
    assert "贵州茅台" in content
    assert "sh600519" in content

    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM alert_logs WHERE stock_code='sh600519' AND signal_type='BUY'"
        ).fetchone()
    assert row is not None
    assert row["sent_status"] == "SUCCESS"
    assert row["trigger_price"] == 108.0
    assert row["baseline_price"] == 100.0
    assert row["used_coeff"] == 1.10
    assert ("sh600519", "2026-08-05", "BUY") in runtime_state.sent_signal_keys


def test_process_buy_alert_skips_second_send_same_day(tmp_path, monkeypatch) -> None:
    sqlite_path = tmp_path / "amtsm.db"
    monkeypatch.setattr(settings, "sqlite_path", str(sqlite_path))
    monkeypatch.setattr(settings, "alert_send_max_retries", 0)
    init_db()
    runtime_state.reset_daily()

    mock_send = MagicMock(
        return_value=SendResult(ok=True, errcode=0, errmsg="ok", message="发送成功")
    )
    monkeypatch.setattr(alert_service.wechat_notifier, "send_text", mock_send)

    first = process_buy_alert(_candidate())
    second = process_buy_alert(_candidate(price=107.5))
    assert first.outcome == AlertOutcome.SENT
    assert second.outcome == AlertOutcome.SKIPPED_MEMORY
    assert mock_send.call_count == 1

    with get_db() as conn:
        count = conn.execute("SELECT COUNT(*) AS c FROM alert_logs").fetchone()["c"]
    assert count == 1


def test_process_buy_alert_logs_failed_with_error_code(tmp_path, monkeypatch) -> None:
    sqlite_path = tmp_path / "amtsm.db"
    monkeypatch.setattr(settings, "sqlite_path", str(sqlite_path))
    monkeypatch.setattr(settings, "alert_send_max_retries", 0)
    init_db()
    runtime_state.reset_daily()

    mock_send = MagicMock(
        return_value=SendResult(
            ok=False,
            errcode=40001,
            errmsg="invalid credential",
            category=ErrorCategory.AUTH,
            message="[auth] errcode=40001 errmsg=invalid credential",
        )
    )
    monkeypatch.setattr(alert_service.wechat_notifier, "send_text", mock_send)

    result = process_buy_alert(_candidate())
    assert result.outcome == AlertOutcome.FAILED
    assert result.error_code == "40001"
    assert ("sh600519", "2026-08-05", "BUY") not in runtime_state.sent_signal_keys

    with get_db() as conn:
        row = conn.execute("SELECT * FROM alert_logs").fetchone()
    assert row["sent_status"] == "FAILED"
    assert row["error_code"] == "40001"
    assert "40001" in (row["error_message"] or "")


def test_process_buy_alert_retries_after_failure(tmp_path, monkeypatch) -> None:
    sqlite_path = tmp_path / "amtsm.db"
    monkeypatch.setattr(settings, "sqlite_path", str(sqlite_path))
    monkeypatch.setattr(settings, "alert_send_max_retries", 0)
    monkeypatch.setattr(settings, "alert_send_retry_backoff_seconds", 0)
    init_db()
    runtime_state.reset_daily()

    fail = SendResult(
        ok=False,
        errcode=45009,
        errmsg="api freq out of limit",
        category=ErrorCategory.API,
        message="rate limited",
    )
    ok = SendResult(ok=True, errcode=0, errmsg="ok", message="发送成功")
    mock_send = MagicMock(side_effect=[fail, ok])
    monkeypatch.setattr(alert_service.wechat_notifier, "send_text", mock_send)

    first = process_buy_alert(_candidate())
    assert first.outcome == AlertOutcome.FAILED

    second = process_buy_alert(_candidate(price=107.0))
    assert second.outcome == AlertOutcome.SENT
    assert mock_send.call_count == 2

    with get_db() as conn:
        rows = conn.execute("SELECT * FROM alert_logs").fetchall()
    assert len(rows) == 1
    assert rows[0]["sent_status"] == "SUCCESS"
    assert rows[0]["trigger_price"] == 107.0


def test_process_buy_alert_retries_within_call(tmp_path, monkeypatch) -> None:
    sqlite_path = tmp_path / "amtsm.db"
    monkeypatch.setattr(settings, "sqlite_path", str(sqlite_path))
    monkeypatch.setattr(settings, "alert_send_max_retries", 2)
    monkeypatch.setattr(settings, "alert_send_retry_backoff_seconds", 0)
    init_db()
    runtime_state.reset_daily()

    fail = SendResult(
        ok=False,
        errcode=-1,
        errmsg="system busy",
        category=ErrorCategory.NETWORK,
        message="network",
    )
    ok = SendResult(ok=True, errcode=0, errmsg="ok", message="发送成功")
    mock_send = MagicMock(side_effect=[fail, fail, ok])
    monkeypatch.setattr(alert_service.wechat_notifier, "send_text", mock_send)

    result = process_buy_alert(_candidate())
    assert result.outcome == AlertOutcome.SENT
    assert mock_send.call_count == 3


def test_process_buy_candidates_ignores_sell(tmp_path, monkeypatch) -> None:
    sqlite_path = tmp_path / "amtsm.db"
    monkeypatch.setattr(settings, "sqlite_path", str(sqlite_path))
    init_db()
    runtime_state.reset_daily()

    mock_send = MagicMock()
    monkeypatch.setattr(alert_service.wechat_notifier, "send_text", mock_send)

    results = process_buy_candidates(
        [
            _candidate(signal_type="SELL", baseline_price=130.0, used_coeff=0.9),
        ]
    )
    assert results == []
    mock_send.assert_not_called()


def test_market_polling_triggers_buy_alert(tmp_path, monkeypatch) -> None:
    sqlite_path = tmp_path / "amtsm.db"
    monkeypatch.setattr(settings, "sqlite_path", str(sqlite_path))
    monkeypatch.setattr(settings, "polling_batch_size", 50)
    monkeypatch.setattr(settings, "alert_send_max_retries", 0)
    init_db()
    runtime_state.reset_daily()
    add_watchlist(WatchlistCreate(stock_code="600519", stock_name="贵州茅台"))

    with get_db() as conn:
        conn.execute(
            "UPDATE strategy_config SET global_buy_x = 1.10, global_sell_y = 0.90 WHERE id = 1"
        )
        conn.execute(
            """
            INSERT INTO daily_baselines (stock_code, trade_date, low_min, high_max, actual_n)
            VALUES ('sh600519', '2026-08-05', 100.0, 130.0, 60)
            """
        )
        conn.commit()

    fake_now = datetime(2026, 8, 5, 10, 0, 0, tzinfo=ZoneInfo("Asia/Shanghai"))

    class _FrozenDateTime:
        def now(self, tz=None):
            if tz is None:
                return fake_now
            return fake_now.astimezone(tz)

    monkeypatch.setattr("app.engine.tasks.datetime", _FrozenDateTime())
    monkeypatch.setattr(
        "app.engine.tasks.fetch_realtime_quotes_batch",
        lambda *args, **kwargs: {
            "sh600519": {
                "stock_name": "贵州茅台",
                "price": 108.0,
                "open": 110.0,
                "prev_close": 111.0,
                "quote_date": "2026-08-05",
                "quote_time": "10:00:00",
                "is_halted": False,
            }
        },
    )
    mock_send = MagicMock(
        return_value=SendResult(ok=True, errcode=0, errmsg="ok", message="发送成功")
    )
    monkeypatch.setattr(alert_service.wechat_notifier, "send_text", mock_send)

    market_polling_task()

    assert runtime_state.signal_state["sh600519"] == "BUY"
    mock_send.assert_called_once()
    with get_db() as conn:
        row = conn.execute(
            "SELECT sent_status, signal_type FROM alert_logs WHERE stock_code='sh600519'"
        ).fetchone()
    assert row["sent_status"] == "SUCCESS"
    assert row["signal_type"] == "BUY"
