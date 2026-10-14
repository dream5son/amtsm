"""Tests for user story 09: sell-signal WeChat alerts."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

import pytest

from app.config import settings
from app.db.connection import get_db
from app.db.init_db import init_db
from app.db.models import AlertLog, DailyBaseline, DailyMarketSnapshot, StrategyConfig
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
from app.services.notifier.email_notifier import email_notifier
from app.services.watchlist_service import add_watchlist
from app.services.wechat_notifier import ErrorCategory, SendResult


def _ok_send() -> SendResult:
    return SendResult(ok=True, errcode=0, errmsg="ok", message="发送成功")


@pytest.fixture(autouse=True)
def _open_alert_window(monkeypatch) -> None:
    """US09 focuses on sell content/limit-up; DND is covered by US10."""
    monkeypatch.setattr(
        "app.services.alert_service.is_alert_window_open",
        lambda **_kwargs: True,
    )


@pytest.fixture(autouse=True)
def _block_real_notifications(monkeypatch) -> None:
    """Never hit WeChat/SMTP even if local .env enables extra channels."""
    monkeypatch.setattr(settings, "notify_channels", "wechat")
    monkeypatch.setattr(settings, "alert_max_per_stock_per_day", 0)
    monkeypatch.setattr(
        alert_service.wechat_notifier,
        "send_text",
        MagicMock(return_value=_ok_send()),
    )
    monkeypatch.setattr(
        email_notifier,
        "send_text",
        MagicMock(return_value=_ok_send()),
    )


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


def _hold(stock_code: str = "sh600519", qty: int = 100) -> None:
    """Seed in-memory holdings so SELL notifications are allowed."""
    runtime_state.position_cache[stock_code] = {"qty": qty}


def _seed_volume_history(session, stock_code: str, before: str, *, n: int = 7, volume: float = 1000.0) -> None:
    day = date.fromisoformat(before) - timedelta(days=1)
    for _ in range(n):
        session.add(
            DailyMarketSnapshot(
                stock_code=stock_code,
                trade_date=day.isoformat(),
                open_price=10.0,
                high_price=10.0,
                low_price=10.0,
                close_price=10.0,
                volume=volume,
            )
        )
        day -= timedelta(days=1)


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
    _hold()

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

    with get_db() as session:
        row = (
            session.query(AlertLog)
            .filter_by(stock_code="sh600519", signal_type="SELL")
            .one_or_none()
        )
    assert row is not None
    assert row.sent_status == "SUCCESS"
    assert row.trigger_price == 117.0
    assert row.baseline_price == 130.0
    assert row.used_coeff == 0.90
    assert ("sh600519", "2026-08-05", "SELL") in runtime_state.sent_signal_keys


def test_process_sell_alert_skips_notify_when_qty_zero(tmp_path, monkeypatch) -> None:
    sqlite_path = tmp_path / "amtsm.db"
    monkeypatch.setattr(settings, "sqlite_path", str(sqlite_path))
    monkeypatch.setattr(settings, "alert_send_max_retries", 0)
    init_db()
    runtime_state.reset_daily()
    runtime_state.position_cache.clear()

    mock_send = MagicMock(
        return_value=SendResult(ok=True, errcode=0, errmsg="ok", message="发送成功")
    )
    monkeypatch.setattr(alert_service.wechat_notifier, "send_text", mock_send)

    result = process_sell_alert(_candidate())
    assert result.outcome == AlertOutcome.SKIPPED_NO_POSITION
    mock_send.assert_not_called()
    assert ("sh600519", "2026-08-05", "SELL") in runtime_state.sent_signal_keys

    with get_db() as session:
        row = session.query(AlertLog).one()
    assert row.sent_status == "SKIPPED"
    assert row.signal_type == "SELL"
    assert row.trigger_price == 117.0
    assert row.baseline_price == 130.0
    assert row.used_coeff == 0.90
    assert "no position" in (row.error_message or "")

    # Second call same day: still no send, no duplicate row.
    second = process_sell_alert(_candidate(price=118.0))
    assert second.outcome == AlertOutcome.SKIPPED_MEMORY
    mock_send.assert_not_called()
    with get_db() as session:
        assert session.query(AlertLog).count() == 1


def test_process_sell_alert_skips_second_send_same_day(tmp_path, monkeypatch) -> None:
    sqlite_path = tmp_path / "amtsm.db"
    monkeypatch.setattr(settings, "sqlite_path", str(sqlite_path))
    monkeypatch.setattr(settings, "alert_send_max_retries", 0)
    init_db()
    runtime_state.reset_daily()
    _hold()

    mock_send = MagicMock(
        return_value=SendResult(ok=True, errcode=0, errmsg="ok", message="发送成功")
    )
    monkeypatch.setattr(alert_service.wechat_notifier, "send_text", mock_send)

    first = process_sell_alert(_candidate())
    second = process_sell_alert(_candidate(price=118.0))
    assert first.outcome == AlertOutcome.SENT
    assert second.outcome == AlertOutcome.SKIPPED_MEMORY
    assert mock_send.call_count == 1

    with get_db() as session:
        count = session.query(AlertLog).count()
    assert count == 1


def test_process_sell_alert_logs_failed_with_error_code(tmp_path, monkeypatch) -> None:
    sqlite_path = tmp_path / "amtsm.db"
    monkeypatch.setattr(settings, "sqlite_path", str(sqlite_path))
    monkeypatch.setattr(settings, "alert_send_max_retries", 0)
    init_db()
    runtime_state.reset_daily()
    _hold()

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

    with get_db() as session:
        row = session.query(AlertLog).one()
    assert row.sent_status == "FAILED"
    assert row.error_code == "40001"
    assert "40001" in (row.error_message or "")


def test_process_sell_alert_retries_after_failure(tmp_path, monkeypatch) -> None:
    sqlite_path = tmp_path / "amtsm.db"
    monkeypatch.setattr(settings, "sqlite_path", str(sqlite_path))
    monkeypatch.setattr(settings, "alert_send_max_retries", 0)
    monkeypatch.setattr(settings, "alert_send_retry_backoff_seconds", 0)
    init_db()
    runtime_state.reset_daily()
    _hold()

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

    with get_db() as session:
        rows = session.query(AlertLog).all()
    assert len(rows) == 1
    assert rows[0].sent_status == "SUCCESS"
    assert rows[0].trigger_price == 118.5


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
    _hold("sh600000")
    _hold("sh600519")

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
    _hold()

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

    with get_db() as session:
        rows = (
            session.query(AlertLog)
            .filter_by(stock_code="sh600519")
            .order_by(AlertLog.signal_type)
            .all()
        )
    assert [r.signal_type for r in rows] == ["BUY", "SELL"]


def test_market_polling_empty_records_sell_without_notify(tmp_path, monkeypatch) -> None:
    """Empty holdings: SELL is recorded in alert_logs, but no WeChat notification."""
    sqlite_path = tmp_path / "amtsm.db"
    monkeypatch.setattr(settings, "sqlite_path", str(sqlite_path))
    monkeypatch.setattr(settings, "polling_batch_size", 50)
    monkeypatch.setattr(settings, "alert_send_max_retries", 0)
    init_db()
    runtime_state.reset_daily()
    add_watchlist(WatchlistCreate(stock_code="600519", stock_name="贵州茅台"))

    with get_db() as session:
        cfg = session.get(StrategyConfig, 1)
        cfg.global_buy_x = 1.10
        cfg.global_sell_y = 0.90
        session.add(
            DailyBaseline(
                stock_code="sh600519",
                trade_date="2026-08-05",
                low_min=100.0,
                high_max=130.0,
                actual_n=60,
            )
        )
        _seed_volume_history(session, "sh600519", "2026-08-05")
        session.commit()

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
                "volume": 1500.0,
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
    assert runtime_state.signal_meta.get("sh600519", {}).get("is_limit_up") is True
    mock_send.assert_not_called()
    with get_db() as session:
        row = session.query(AlertLog).filter_by(stock_code="sh600519").one()
    assert row.sent_status == "SKIPPED"
    assert row.signal_type == "SELL"
    assert row.trigger_price == 121.0
    assert row.baseline_price == 130.0
    assert row.used_coeff == 0.90
    assert "no position" in (row.error_message or "")


def test_market_polling_holding_tech_sell_notifies(tmp_path, monkeypatch) -> None:
    """Holding + enable_tech_sell_while_holding: SELL still notifies."""
    from app.db.models import Position

    sqlite_path = tmp_path / "amtsm.db"
    monkeypatch.setattr(settings, "sqlite_path", str(sqlite_path))
    monkeypatch.setattr(settings, "polling_batch_size", 50)
    monkeypatch.setattr(settings, "alert_send_max_retries", 0)
    init_db()
    runtime_state.reset_daily()
    add_watchlist(WatchlistCreate(stock_code="600519", stock_name="贵州茅台"))

    with get_db() as session:
        cfg = session.get(StrategyConfig, 1)
        cfg.global_buy_x = 1.10
        cfg.global_sell_y = 0.90
        cfg.enable_tech_sell_while_holding = 1
        # Keep risk exits from firing so tech SELL can surface.
        cfg.global_stop_loss_pct = 0.50
        cfg.global_break_even_trigger_pct = 0.99
        session.add(
            DailyBaseline(
                stock_code="sh600519",
                trade_date="2026-08-05",
                low_min=100.0,
                high_max=130.0,
                actual_n=60,
            )
        )
        session.add(
            Position(
                stock_code="sh600519",
                qty=100,
                avg_cost=100.0,
                highest_since_hold=120.0,
                stop_price=85.0,
                position_status="HOLDING",
            )
        )
        _seed_volume_history(session, "sh600519", "2026-08-05")
        session.commit()

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
                "price": 121.0,
                "open": 110.0,
                "prev_close": 110.0,
                "volume": 1500.0,
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
    with get_db() as session:
        row = session.query(AlertLog).filter_by(stock_code="sh600519").one()
    assert row.sent_status == "SUCCESS"
    assert row.signal_type == "SELL"
    assert row.trigger_price == 121.0
    assert row.baseline_price == 130.0
    assert row.used_coeff == 0.90
