"""Tests for user story 09: sell-signal WeChat alerts."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

from app.config import settings
from app.db.connection import get_db
from app.db.init_db import init_db
from app.engine.state import runtime_state
from app.engine.tasks import market_polling_task
from app.schemas.watchlist import WatchlistCreate
from app.services import alert_service
from app.services.alert_service import (
    LIMIT_BOARD_NOTE,
    AlertOutcome,
    format_sell_message,
    is_limit_up,
    process_buy_candidates,
    process_sell_alert,
    process_sell_candidates,
)
from app.services.watchlist_service import add_watchlist
from app.services.wechat_notifier import ErrorCategory, SendResult


def _candidate(**overrides) -> dict:
    base = {
        "stock_code": "sh600519",
        "stock_name": "贵州茅台",
        "signal_type": "SELL",
        "price": 117.0,
        "trade_date": "2026-08-05",
        "baseline_price": 130.0,
        "used_coeff": 0.90,
        "actual_n": 60,
        "prev_close": 110.0,
        "is_limit_up": False,
    }
    base.update(overrides)
    return base


def test_format_sell_message_contains_required_fields() -> None:
    text = format_sell_message(
        stock_name="贵州茅台",
        stock_code="sh600519",
        price=117.0,
        actual_n=60,
        high_max=130.0,
        used_coeff=0.9,
    )
    assert "贵州茅台" in text
    assert "sh600519" in text
    assert "117" in text
    assert "60" in text
    assert "130" in text
    assert "0.9" in text
    assert "卖出信号" in text
    assert LIMIT_BOARD_NOTE not in text


def test_format_sell_message_appends_limit_up_note() -> None:
    text = format_sell_message(
        stock_name="贵州茅台",
        stock_code="sh600519",
        price=121.0,
        actual_n=60,
        high_max=130.0,
        used_coeff=0.9,
        is_limit_up=True,
    )
    assert text.endswith(LIMIT_BOARD_NOTE)


def test_is_limit_up_main_board() -> None:
    assert is_limit_up(
        stock_code="sh600519",
        price=121.0,
        prev_close=110.0,
        stock_name="贵州茅台",
    )
    assert not is_limit_up(
        stock_code="sh600519",
        price=120.0,
        prev_close=110.0,
        stock_name="贵州茅台",
    )


def test_is_limit_up_chinext_20_percent() -> None:
    assert is_limit_up(
        stock_code="sz300750",
        price=24.0,
        prev_close=20.0,
        stock_name="宁德时代",
    )
    assert not is_limit_up(
        stock_code="sz300750",
        price=23.9,
        prev_close=20.0,
        stock_name="宁德时代",
    )


def test_process_sell_alert_sends_and_logs_success(tmp_path, monkeypatch) -> None:
    sqlite_path = tmp_path / "amtsm.db"
    monkeypatch.setattr(settings, "sqlite_path", str(sqlite_path))
    monkeypatch.setattr(settings, "alert_send_max_retries", 0)
    init_db()
    runtime_state.reset_daily()

    mock_send = MagicMock(
        return_value=SendResult(ok=True, errcode=0, errmsg="ok", message="发送成功")
    )
    monkeypatch.setattr(alert_service.wechat_notifier, "send_text", mock_send)

    result = process_sell_alert(_candidate())
    assert result.outcome == AlertOutcome.SENT
    mock_send.assert_called_once()
    content = mock_send.call_args.args[0]
    assert "贵州茅台" in content
    assert "sh600519" in content
    assert "卖出信号" in content

    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM alert_logs WHERE stock_code='sh600519' AND signal_type='SELL'"
        ).fetchone()
    assert row is not None
    assert row["sent_status"] == "SUCCESS"
    assert row["trigger_price"] == 117.0
    assert row["baseline_price"] == 130.0
    assert row["used_coeff"] == 0.90
    assert ("sh600519", "2026-08-05", "SELL") in runtime_state.sent_signal_keys


def test_process_sell_alert_skips_second_send_same_day(tmp_path, monkeypatch) -> None:
    sqlite_path = tmp_path / "amtsm.db"
    monkeypatch.setattr(settings, "sqlite_path", str(sqlite_path))
    monkeypatch.setattr(settings, "alert_send_max_retries", 0)
    init_db()
    runtime_state.reset_daily()

    mock_send = MagicMock(
        return_value=SendResult(ok=True, errcode=0, errmsg="ok", message="发送成功")
    )
    monkeypatch.setattr(alert_service.wechat_notifier, "send_text", mock_send)

    first = process_sell_alert(_candidate())
    second = process_sell_alert(_candidate(price=118.0))
    assert first.outcome == AlertOutcome.SENT
    assert second.outcome == AlertOutcome.SKIPPED_MEMORY
    assert mock_send.call_count == 1

    with get_db() as conn:
        count = conn.execute("SELECT COUNT(*) AS c FROM alert_logs").fetchone()["c"]
    assert count == 1


def test_process_sell_alert_logs_failed_with_error_code(tmp_path, monkeypatch) -> None:
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

    result = process_sell_alert(_candidate())
    assert result.outcome == AlertOutcome.FAILED
    assert result.error_code == "40001"
    assert ("sh600519", "2026-08-05", "SELL") not in runtime_state.sent_signal_keys

    with get_db() as conn:
        row = conn.execute("SELECT * FROM alert_logs").fetchone()
    assert row["sent_status"] == "FAILED"
    assert row["error_code"] == "40001"
    assert "40001" in (row["error_message"] or "")


def test_process_sell_alert_retries_after_failure(tmp_path, monkeypatch) -> None:
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

    first = process_sell_alert(_candidate())
    assert first.outcome == AlertOutcome.FAILED

    second = process_sell_alert(_candidate(price=118.5))
    assert second.outcome == AlertOutcome.SENT
    assert mock_send.call_count == 2

    with get_db() as conn:
        rows = conn.execute("SELECT * FROM alert_logs").fetchall()
    assert len(rows) == 1
    assert rows[0]["sent_status"] == "SUCCESS"
    assert rows[0]["trigger_price"] == 118.5


def test_process_sell_candidates_ignores_buy(tmp_path, monkeypatch) -> None:
    sqlite_path = tmp_path / "amtsm.db"
    monkeypatch.setattr(settings, "sqlite_path", str(sqlite_path))
    init_db()
    runtime_state.reset_daily()

    mock_send = MagicMock()
    monkeypatch.setattr(alert_service.wechat_notifier, "send_text", mock_send)

    results = process_sell_candidates(
        [
            _candidate(signal_type="BUY", baseline_price=100.0, used_coeff=1.1),
        ]
    )
    assert results == []
    mock_send.assert_not_called()


def test_process_sell_candidates_isolates_failures(tmp_path, monkeypatch) -> None:
    sqlite_path = tmp_path / "amtsm.db"
    monkeypatch.setattr(settings, "sqlite_path", str(sqlite_path))
    monkeypatch.setattr(settings, "alert_send_max_retries", 0)
    init_db()
    runtime_state.reset_daily()

    def _send(content: str) -> SendResult:
        if "sh600000" in content:
            raise RuntimeError("boom")
        return SendResult(ok=True, errcode=0, errmsg="ok", message="发送成功")

    monkeypatch.setattr(alert_service.wechat_notifier, "send_text", _send)

    results = process_sell_candidates(
        [
            _candidate(stock_code="sh600000", stock_name="浦发银行"),
            _candidate(stock_code="sh600519", stock_name="贵州茅台"),
        ]
    )
    assert len(results) == 2
    assert results[0].outcome == AlertOutcome.FAILED
    assert results[1].outcome == AlertOutcome.SENT


def test_buy_and_sell_same_day_independent_freq(tmp_path, monkeypatch) -> None:
    sqlite_path = tmp_path / "amtsm.db"
    monkeypatch.setattr(settings, "sqlite_path", str(sqlite_path))
    monkeypatch.setattr(settings, "alert_send_max_retries", 0)
    init_db()
    runtime_state.reset_daily()

    mock_send = MagicMock(
        return_value=SendResult(ok=True, errcode=0, errmsg="ok", message="发送成功")
    )
    monkeypatch.setattr(alert_service.wechat_notifier, "send_text", mock_send)

    buy = {
        "stock_code": "sh600519",
        "stock_name": "贵州茅台",
        "signal_type": "BUY",
        "price": 108.0,
        "trade_date": "2026-08-05",
        "baseline_price": 100.0,
        "used_coeff": 1.10,
        "actual_n": 60,
    }
    sell = _candidate()
    buy_results = process_buy_candidates([buy])
    sell_results = process_sell_candidates([sell])
    assert buy_results[0].outcome == AlertOutcome.SENT
    assert sell_results[0].outcome == AlertOutcome.SENT
    assert mock_send.call_count == 2

    with get_db() as conn:
        rows = conn.execute(
            "SELECT signal_type FROM alert_logs WHERE stock_code='sh600519' ORDER BY signal_type"
        ).fetchall()
    assert [r["signal_type"] for r in rows] == ["BUY", "SELL"]


def test_market_polling_triggers_sell_alert(tmp_path, monkeypatch) -> None:
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
    # sell_threshold = 130 * 0.9 = 117; price 121 also hits main-board limit-up vs 110
    monkeypatch.setattr(
        "app.engine.tasks.fetch_realtime_quotes_batch",
        lambda *args, **kwargs: {
            "sh600519": {
                "stock_name": "贵州茅台",
                "price": 121.0,
                "open": 110.0,
                "prev_close": 110.0,
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

    assert runtime_state.signal_state["sh600519"] == "SELL"
    mock_send.assert_called_once()
    content = mock_send.call_args.args[0]
    assert "卖出信号" in content
    assert LIMIT_BOARD_NOTE in content
    with get_db() as conn:
        row = conn.execute(
            "SELECT sent_status, signal_type, trigger_price, baseline_price, used_coeff "
            "FROM alert_logs WHERE stock_code='sh600519'"
        ).fetchone()
    assert row["sent_status"] == "SUCCESS"
    assert row["signal_type"] == "SELL"
    assert row["trigger_price"] == 121.0
    assert row["baseline_price"] == 130.0
    assert row["used_coeff"] == 0.90
