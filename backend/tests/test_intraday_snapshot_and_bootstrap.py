from datetime import date, datetime, time
from unittest.mock import patch
from zoneinfo import ZoneInfo

from app.config import settings
from app.db.init_db import init_db
from app.engine.scheduler import enqueue_snapshot_bootstrap, get_scheduler
from app.engine.tasks import (
    bootstrap_watchlist_snapshot,
    get_snapshot,
    intraday_snapshot_task,
)
from app.schemas.watchlist import WatchlistCreate
from app.services.market_data_service import MissingTradeDayBarError
from app.services.watchlist_service import add_watchlist

SH_TZ = ZoneInfo("Asia/Shanghai")


def _setup_db(tmp_path, monkeypatch):
    sqlite_path = tmp_path / "amtsm.db"
    monkeypatch.setattr(settings, "sqlite_path", str(sqlite_path))
    init_db()


def test_bootstrap_watchlist_snapshot_from_realtime(tmp_path, monkeypatch) -> None:
    _setup_db(tmp_path, monkeypatch)

    def fake_quotes(codes, **_kwargs):
        return {
            codes[0]: {
                "stock_name": "贵州茅台",
                "price": 101.5,
                "open": 100.0,
                "prev_close": 99.0,
                "high": 102.0,
                "low": 99.5,
                "volume": 12345.0,
                "quote_date": "2026-08-05",
                "quote_time": "10:05:01",
                "is_halted": False,
                "has_quote": True,
            }
        }

    monkeypatch.setattr(
        "app.engine.tasks.fetch_realtime_quotes_batch", fake_quotes
    )
    with patch("app.engine.tasks.fetch_trade_day_bar") as mock_bar:
        bootstrap_watchlist_snapshot("sh600519")
        mock_bar.assert_not_called()

    snap = get_snapshot("sh600519", "2026-08-05")
    assert snap is not None
    assert snap["close_price"] == 101.5
    assert snap["open_price"] == 100.0
    assert snap["high_price"] == 102.0
    assert snap["low_price"] == 99.5
    assert snap["volume"] == 12345.0


def test_bootstrap_watchlist_snapshot_falls_back_to_daily_bar(
    tmp_path, monkeypatch
) -> None:
    _setup_db(tmp_path, monkeypatch)

    monkeypatch.setattr(
        "app.engine.tasks.fetch_realtime_quotes_batch",
        lambda *_a, **_k: {
            "sh600519": {
                "stock_name": "贵州茅台",
                "price": None,
                "open": None,
                "prev_close": None,
                "high": None,
                "low": None,
                "volume": None,
                "quote_date": None,
                "quote_time": None,
                "is_halted": False,
                "has_quote": False,
            }
        },
    )

    def fake_bar(code, trade_date, **_kwargs):
        if trade_date != date(2026, 8, 5):
            raise MissingTradeDayBarError("missing")
        return {
            "date": "2026-08-05",
            "open": 10.0,
            "high": 11.0,
            "low": 9.5,
            "close": 10.5,
            "volume": 1000.0,
            "turnover_rate": 1.1,
        }

    monkeypatch.setattr(
        "app.engine.tasks.datetime",
        type(
            "FixedDatetime",
            (),
            {
                "now": staticmethod(
                    lambda tz=None: datetime(2026, 8, 7, 10, 0, tzinfo=SH_TZ)
                )
            },
        ),
    )
    # Patch is_trading_day to treat Aug 5-7 as trading days for lookback.
    monkeypatch.setattr(
        "app.engine.tasks.fetch_trade_day_bar", fake_bar
    )
    # Walk: 2026-08-07 (Thu), 08-06 (Wed), 08-05 (Tue) — fake_bar only succeeds on 08-05
    monkeypatch.setattr(
        "app.engine.tasks.is_trading_day",
        lambda d: d.weekday() < 5,
    )

    bootstrap_watchlist_snapshot("sh600519")
    snap = get_snapshot("sh600519", "2026-08-05")
    assert snap is not None
    assert snap["close_price"] == 10.5
    assert snap["turnover_rate"] == 1.1


def test_bootstrap_watchlist_snapshot_swallows_errors(tmp_path, monkeypatch) -> None:
    _setup_db(tmp_path, monkeypatch)

    def boom(*_a, **_k):
        raise RuntimeError("network down")

    monkeypatch.setattr("app.engine.tasks.fetch_realtime_quotes_batch", boom)
    monkeypatch.setattr("app.engine.tasks.fetch_trade_day_bar", boom)
    # Must not raise
    bootstrap_watchlist_snapshot("sh600519")


def test_add_watchlist_does_not_raise_when_enqueue_noop(tmp_path, monkeypatch) -> None:
    _setup_db(tmp_path, monkeypatch)
    # Scheduler not running → enqueue is no-op
    assert get_scheduler() is None or not get_scheduler().running
    add_watchlist(WatchlistCreate(stock_code="600519", stock_name="贵州茅台"))
    enqueue_snapshot_bootstrap("sh600519")  # still no-op


def test_intraday_snapshot_task_skips_outside_session(tmp_path, monkeypatch) -> None:
    _setup_db(tmp_path, monkeypatch)
    add_watchlist(WatchlistCreate(stock_code="600519", stock_name="贵州茅台"))

    monkeypatch.setattr(
        "app.engine.tasks.datetime",
        type(
            "FixedDatetime",
            (),
            {
                "now": staticmethod(
                    lambda tz=None: datetime(2026, 8, 5, 12, 0, tzinfo=SH_TZ)
                )
            },
        ),
    )
    called = {"n": 0}

    def mark_called(*_a, **_k):
        called["n"] += 1
        return {}

    monkeypatch.setattr(
        "app.engine.tasks.fetch_realtime_quotes_batch", mark_called
    )
    intraday_snapshot_task()
    assert called["n"] == 0


def test_intraday_snapshot_task_upserts_during_session(tmp_path, monkeypatch) -> None:
    _setup_db(tmp_path, monkeypatch)
    add_watchlist(WatchlistCreate(stock_code="600519", stock_name="贵州茅台"))

    monkeypatch.setattr(
        "app.engine.tasks.datetime",
        type(
            "FixedDatetime",
            (),
            {
                "now": staticmethod(
                    lambda tz=None: datetime(2026, 8, 5, 10, 5, tzinfo=SH_TZ)
                )
            },
        ),
    )
    monkeypatch.setattr(
        "app.engine.tasks.is_trading_day", lambda _d: True
    )
    monkeypatch.setattr(
        "app.engine.tasks.is_in_trading_session",
        lambda t: t == time(10, 5) or True,
    )

    def fake_quotes(codes, **_kwargs):
        return {
            code: {
                "stock_name": "贵州茅台",
                "price": 110.0,
                "open": 108.0,
                "prev_close": 107.0,
                "high": 111.0,
                "low": 107.5,
                "volume": 999.0,
                "quote_date": "2026-08-05",
                "quote_time": "10:05:01",
                "is_halted": False,
                "has_quote": True,
            }
            for code in codes
        }

    monkeypatch.setattr(
        "app.engine.tasks.fetch_realtime_quotes_batch", fake_quotes
    )
    intraday_snapshot_task()

    snap = get_snapshot("sh600519", "2026-08-05")
    assert snap is not None
    assert snap["close_price"] == 110.0
    assert snap["open_price"] == 108.0
    assert snap["high_price"] == 111.0
    assert snap["volume"] == 999.0
