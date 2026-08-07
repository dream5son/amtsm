from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from app.config import settings
from app.db.connection import get_db
from app.db.init_db import init_db
from app.db.models import DailyBaseline, StrategyConfig
from app.engine.state import runtime_state
from app.engine.tasks import (
    _ensure_baseline_cache_for_trade_date,
    is_in_trading_session,
    is_trading_day,
    market_polling_task,
)
from app.schemas.watchlist import WatchlistCreate
from app.services.watchlist_service import add_watchlist


def test_is_in_trading_session_boundary() -> None:
    assert is_in_trading_session(time(9, 30, 0))
    assert is_in_trading_session(time(11, 30, 0))
    assert is_in_trading_session(time(13, 0, 0))
    assert is_in_trading_session(time(15, 0, 0))
    assert not is_in_trading_session(time(11, 31, 0))
    assert not is_in_trading_session(time(12, 30, 0))
    assert not is_in_trading_session(time(15, 1, 0))


def test_is_trading_day_weekend() -> None:
    assert not is_trading_day(date(2026, 8, 8))  # Saturday
    assert not is_trading_day(date(2026, 8, 9))  # Sunday
    assert is_trading_day(date(2026, 8, 10))  # Monday


def test_restore_baseline_cache_from_db(tmp_path, monkeypatch) -> None:
    sqlite_path = tmp_path / "amtsm.db"
    monkeypatch.setattr(settings, "sqlite_path", str(sqlite_path))
    init_db()
    runtime_state.reset_daily()

    with get_db() as session:
        session.add(
            DailyBaseline(
                stock_code="sh600519",
                trade_date="2026-08-05",
                low_min=100.0,
                high_max=120.0,
                actual_n=60,
            )
        )
        session.commit()

    restored = _ensure_baseline_cache_for_trade_date("2026-08-05")
    assert restored
    assert runtime_state.baseline_cache["sh600519"]["low_min"] == 100.0


def test_market_polling_generates_signal_and_keeps_previous_signal(tmp_path, monkeypatch) -> None:
    sqlite_path = tmp_path / "amtsm.db"
    monkeypatch.setattr(settings, "sqlite_path", str(sqlite_path))
    monkeypatch.setattr(settings, "polling_batch_size", 50)
    monkeypatch.setattr(settings, "polling_request_timeout_seconds", 1.0)
    monkeypatch.setattr(settings, "polling_request_retries", 0)

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
        session.commit()

    fake_now = datetime(2026, 8, 5, 10, 0, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    monkeypatch.setattr("app.engine.tasks.datetime", _FrozenDateTime(fake_now))
    monkeypatch.setattr("app.engine.tasks.process_buy_candidates", lambda candidates: [])
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

    market_polling_task()
    assert runtime_state.signal_state["sh600519"] == "BUY"

    monkeypatch.setattr(
        "app.engine.tasks.fetch_realtime_quotes_batch",
        lambda *args, **kwargs: {
            "sh600519": {
                "stock_name": "贵州茅台",
                "price": 115.0,
                "open": 110.0,
                "prev_close": 111.0,
                "quote_date": "2026-08-05",
                "quote_time": "10:01:00",
                "is_halted": False,
            }
        },
    )
    market_polling_task()
    assert runtime_state.signal_state["sh600519"] == "BUY"


def test_market_polling_skips_outside_session(tmp_path, monkeypatch) -> None:
    sqlite_path = tmp_path / "amtsm.db"
    monkeypatch.setattr(settings, "sqlite_path", str(sqlite_path))
    init_db()
    runtime_state.reset_daily()
    add_watchlist(WatchlistCreate(stock_code="600519", stock_name="贵州茅台"))

    fake_now = datetime(2026, 8, 5, 11, 31, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    monkeypatch.setattr("app.engine.tasks.datetime", _FrozenDateTime(fake_now))

    called = {"hit": False}

    def _mark_called(*args, **kwargs):
        called["hit"] = True
        return {}

    monkeypatch.setattr("app.engine.tasks.fetch_realtime_quotes_batch", _mark_called)
    market_polling_task()
    assert not called["hit"]


class _FrozenDateTime:
    def __init__(self, now: datetime) -> None:
        self._now = now

    def now(self, tz=None) -> datetime:
        if tz is None:
            return self._now
        return self._now.astimezone(tz)
