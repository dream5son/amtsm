from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from app.config import settings
from app.db.connection import get_db
from app.db.init_db import init_db
from app.db.models import (
    DailyBaseline,
    DailyMarketSnapshot,
    StockYearRange,
    StrategyConfig,
)
from app.engine.state import runtime_state
from app.engine.tasks import (
    bootstrap_watchlist_snapshot,
    daily_snapshot_task,
    market_polling_task,
    startup_market_data_catchup,
)
from app.schemas.watchlist import WatchlistCreate
from app.services.watchlist_service import (
    add_watchlist,
    list_watchlist,
    remove_watchlist,
)
from app.services.year_range_service import (
    compute_water_level,
    refresh_year_range_from_snapshots,
    refresh_year_range_stats,
    year_high_lows_from_rows,
    year_range_since,
)


def test_compute_water_level_edges() -> None:
    assert compute_water_level(100.0, 100.0, 200.0) == 0.0
    assert compute_water_level(200.0, 100.0, 200.0) == 1.0
    assert compute_water_level(150.0, 100.0, 200.0) == 0.5
    assert compute_water_level(50.0, 100.0, 200.0) == 0.0
    assert compute_water_level(250.0, 100.0, 200.0) == 1.0
    assert compute_water_level(120.0, 120.0, 120.0) == 0.5


def test_year_range_since_is_365_calendar_days() -> None:
    assert year_range_since("2026-08-05") == "2025-08-05"


def test_year_high_lows_from_rows_skips_null_aggregates() -> None:
    assert year_high_lows_from_rows(
        [
            ("sh600519", None, 200.0),
            ("sz000001", 8.0, None),
            (None, 1.0, 2.0),
            ("sh601398", 5.0, 9.0),
        ]
    ) == {"sh601398": (5.0, 9.0)}


def test_refresh_year_range_stats_uses_quote_when_no_snapshots(tmp_path, monkeypatch) -> None:
    sqlite_path = tmp_path / "amtsm.db"
    monkeypatch.setattr(settings, "sqlite_path", str(sqlite_path))
    init_db()
    runtime_state.reset_daily()
    add_watchlist(WatchlistCreate(stock_code="600519", stock_name="贵州茅台"))

    written = refresh_year_range_stats(
        "2026-08-05",
        {
            "sh600519": {
                "price": 150.0,
                "high": 155.0,
                "low": 148.0,
                "quote_date": "2026-08-05",
            }
        },
        ["sh600519"],
    )
    assert written == 1

    with get_db() as session:
        row = session.get(StockYearRange, "sh600519")
        assert row is not None
        assert row.year_low == 148.0
        assert row.year_high == 155.0
        assert row.current_price == 150.0
        assert row.water_level == compute_water_level(150.0, 148.0, 155.0)


def test_refresh_year_range_from_snapshots_uses_latest_close(tmp_path, monkeypatch) -> None:
    sqlite_path = tmp_path / "amtsm.db"
    monkeypatch.setattr(settings, "sqlite_path", str(sqlite_path))
    init_db()
    runtime_state.reset_daily()
    add_watchlist(WatchlistCreate(stock_code="600519", stock_name="贵州茅台"))

    with get_db() as session:
        session.add(
            DailyMarketSnapshot(
                stock_code="sh600519",
                trade_date="2026-01-15",
                open_price=90.0,
                high_price=200.0,
                low_price=80.0,
                close_price=95.0,
                volume=1000.0,
            )
        )
        session.add(
            DailyMarketSnapshot(
                stock_code="sh600519",
                trade_date="2026-08-05",
                open_price=140.0,
                high_price=160.0,
                low_price=130.0,
                close_price=140.0,
                volume=1000.0,
            )
        )
        session.commit()

    written = refresh_year_range_from_snapshots(["sh600519"], as_of="2026-08-05")
    assert written == 1

    with get_db() as session:
        row = session.get(StockYearRange, "sh600519")
        assert row is not None
        assert row.year_low == 80.0
        assert row.year_high == 200.0
        assert row.current_price == 140.0
        assert row.water_level == 0.5


def test_refresh_year_range_stats_uses_last_year_snapshots(tmp_path, monkeypatch) -> None:
    sqlite_path = tmp_path / "amtsm.db"
    monkeypatch.setattr(settings, "sqlite_path", str(sqlite_path))
    init_db()
    runtime_state.reset_daily()
    add_watchlist(WatchlistCreate(stock_code="600519", stock_name="贵州茅台"))

    trade_date = "2026-08-05"
    since = year_range_since(trade_date)
    too_old = (date.fromisoformat(since) - timedelta(days=1)).isoformat()
    with get_db() as session:
        session.add(
            DailyMarketSnapshot(
                stock_code="sh600519",
                trade_date=too_old,
                open_price=40.0,
                high_price=45.0,
                low_price=30.0,
                close_price=42.0,
                volume=1000.0,
            )
        )
        session.add(
            DailyMarketSnapshot(
                stock_code="sh600519",
                trade_date=since,
                open_price=90.0,
                high_price=100.0,
                low_price=80.0,
                close_price=95.0,
                volume=1000.0,
            )
        )
        session.add(
            DailyMarketSnapshot(
                stock_code="sh600519",
                trade_date="2026-01-15",
                open_price=140.0,
                high_price=200.0,
                low_price=130.0,
                close_price=150.0,
                volume=1000.0,
            )
        )
        session.commit()

    written = refresh_year_range_stats(
        trade_date,
        {
            "sh600519": {
                "price": 150.0,
                "high": 155.0,
                "low": 148.0,
                "quote_date": trade_date,
            }
        },
        ["sh600519"],
    )
    assert written == 1

    with get_db() as session:
        row = session.get(StockYearRange, "sh600519")
        assert row is not None
        assert row.year_low == 80.0
        assert row.year_high == 200.0
        assert row.current_price == 150.0
        assert row.water_level == compute_water_level(150.0, 80.0, 200.0)

    item = list_watchlist()[0]
    assert item["year_low"] == 80.0
    assert item["year_high"] == 200.0
    assert item["water_level"] == compute_water_level(150.0, 80.0, 200.0)


def test_list_watchlist_computes_year_range_from_snapshots_when_missing(
    tmp_path, monkeypatch
) -> None:
    sqlite_path = tmp_path / "amtsm.db"
    monkeypatch.setattr(settings, "sqlite_path", str(sqlite_path))
    init_db()
    runtime_state.reset_daily()
    add_watchlist(WatchlistCreate(stock_code="600519", stock_name="贵州茅台"))

    with get_db() as session:
        session.add(
            DailyMarketSnapshot(
                stock_code="sh600519",
                trade_date="2026-01-15",
                open_price=90.0,
                high_price=200.0,
                low_price=80.0,
                close_price=140.0,
                volume=1000.0,
            )
        )
        session.commit()

    runtime_state.signal_trade_date = "2026-08-05"
    item = list_watchlist()[0]
    assert item["year_low"] == 80.0
    assert item["year_high"] == 200.0
    assert item["latest_price"] == 140.0
    assert item["water_level"] == compute_water_level(140.0, 80.0, 200.0)
    with get_db() as session:
        row = session.get(StockYearRange, "sh600519")
        assert row is not None
        assert row.current_price == 140.0
        assert row.year_low == 80.0
        assert row.year_high == 200.0


def test_list_watchlist_overlays_water_level_from_live_quote(tmp_path, monkeypatch) -> None:
    sqlite_path = tmp_path / "amtsm.db"
    monkeypatch.setattr(settings, "sqlite_path", str(sqlite_path))
    init_db()
    runtime_state.reset_daily()
    add_watchlist(WatchlistCreate(stock_code="600519", stock_name="贵州茅台"))

    with get_db() as session:
        session.add(
            StockYearRange(
                stock_code="sh600519",
                year_low=100.0,
                year_high=200.0,
                current_price=150.0,
                water_level=0.5,
            )
        )
        session.commit()

    runtime_state.signal_trade_date = "2026-08-05"
    runtime_state.last_quotes["sh600519"] = {
        "price": 175.0,
        "open": 170.0,
        "quote_date": "2026-08-05",
    }
    item = list_watchlist()[0]
    assert item["latest_price"] == 175.0
    assert item["year_low"] == 100.0
    assert item["year_high"] == 200.0
    assert item["water_level"] == 0.75


def test_remove_watchlist_deletes_year_range(tmp_path, monkeypatch) -> None:
    sqlite_path = tmp_path / "amtsm.db"
    monkeypatch.setattr(settings, "sqlite_path", str(sqlite_path))
    init_db()
    runtime_state.reset_daily()
    add_watchlist(WatchlistCreate(stock_code="600519", stock_name="贵州茅台"))

    with get_db() as session:
        session.add(
            StockYearRange(
                stock_code="sh600519",
                year_low=100.0,
                year_high=200.0,
                current_price=150.0,
                water_level=0.5,
            )
        )
        session.commit()

    assert remove_watchlist("sh600519") == 1
    with get_db() as session:
        assert session.get(StockYearRange, "sh600519") is None


class _FrozenDateTime:
    def __init__(self, now: datetime) -> None:
        self._now = now

    def now(self, tz=None) -> datetime:
        if tz is None:
            return self._now
        return self._now.astimezone(tz)


def test_market_polling_persists_year_range_water_level(tmp_path, monkeypatch) -> None:
    sqlite_path = tmp_path / "amtsm.db"
    monkeypatch.setattr(settings, "sqlite_path", str(sqlite_path))
    monkeypatch.setattr(settings, "polling_batch_size", 50)
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
        session.add(
            DailyMarketSnapshot(
                stock_code="sh600519",
                trade_date="2026-01-15",
                open_price=90.0,
                high_price=200.0,
                low_price=80.0,
                close_price=120.0,
                volume=1000.0,
            )
        )
        session.commit()

    fake_now = datetime(2026, 8, 5, 10, 0, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    monkeypatch.setattr("app.engine.tasks.datetime", _FrozenDateTime(fake_now))
    monkeypatch.setattr("app.engine.tasks.process_buy_candidates", lambda candidates: [])
    monkeypatch.setattr("app.engine.tasks.process_sell_candidates", lambda candidates: [])
    monkeypatch.setattr("app.engine.tasks.process_risk_candidates", lambda candidates: [])
    monkeypatch.setattr(
        "app.engine.tasks.fetch_realtime_quotes_batch",
        lambda *args, **kwargs: {
            "sh600519": {
                "stock_name": "贵州茅台",
                "price": 140.0,
                "open": 138.0,
                "high": 142.0,
                "low": 137.0,
                "prev_close": 138.0,
                "volume": 1500.0,
                "quote_date": "2026-08-05",
                "quote_time": "10:00:00",
                "is_halted": False,
            }
        },
    )

    market_polling_task()

    with get_db() as session:
        row = session.get(StockYearRange, "sh600519")
        assert row is not None
        assert row.year_low == 80.0
        assert row.year_high == 200.0
        assert row.current_price == 140.0
        assert abs(row.water_level - 0.5) < 1e-9

    item = list_watchlist()[0]
    assert item["year_low"] == 80.0
    assert item["year_high"] == 200.0
    assert abs(item["water_level"] - 0.5) < 1e-9


def test_bootstrap_watchlist_snapshot_persists_year_range(tmp_path, monkeypatch) -> None:
    sqlite_path = tmp_path / "amtsm.db"
    monkeypatch.setattr(settings, "sqlite_path", str(sqlite_path))
    init_db()
    runtime_state.reset_daily()
    add_watchlist(WatchlistCreate(stock_code="600519", stock_name="贵州茅台"))

    with get_db() as session:
        session.add(
            DailyMarketSnapshot(
                stock_code="sh600519",
                trade_date="2026-01-15",
                open_price=90.0,
                high_price=200.0,
                low_price=80.0,
                close_price=120.0,
                volume=1000.0,
            )
        )
        session.commit()

    monkeypatch.setattr(
        "app.engine.tasks.fetch_realtime_quotes_batch",
        lambda *args, **kwargs: {
            "sh600519": {
                "stock_name": "贵州茅台",
                "price": 140.0,
                "open": 138.0,
                "high": 142.0,
                "low": 137.0,
                "volume": 1500.0,
                "quote_date": "2026-08-05",
                "quote_time": "10:00:00",
                "is_halted": False,
                "has_quote": True,
            }
        },
    )

    bootstrap_watchlist_snapshot("sh600519")

    with get_db() as session:
        row = session.get(StockYearRange, "sh600519")
        assert row is not None
        assert row.year_low == 80.0
        assert row.year_high == 200.0
        assert row.current_price == 140.0
        assert abs(row.water_level - 0.5) < 1e-9


def test_daily_snapshot_task_persists_year_range(tmp_path, monkeypatch) -> None:
    sqlite_path = tmp_path / "amtsm.db"
    monkeypatch.setattr(settings, "sqlite_path", str(sqlite_path))
    monkeypatch.setattr(settings, "snapshot_fetch_retries", 0)
    init_db()
    runtime_state.reset_daily()
    add_watchlist(WatchlistCreate(stock_code="600519", stock_name="贵州茅台"))

    with get_db() as session:
        session.add(
            DailyMarketSnapshot(
                stock_code="sh600519",
                trade_date="2026-01-15",
                open_price=90.0,
                high_price=200.0,
                low_price=80.0,
                close_price=120.0,
                volume=1000.0,
            )
        )
        session.commit()

    fake_now = datetime(2026, 8, 5, 15, 30, tzinfo=ZoneInfo("Asia/Shanghai"))
    monkeypatch.setattr("app.engine.tasks.datetime", _FrozenDateTime(fake_now))
    monkeypatch.setattr(
        "app.engine.tasks.fetch_trade_day_bar",
        lambda *args, **kwargs: {
            "date": "2026-08-05",
            "open": 140.0,
            "high": 160.0,
            "low": 130.0,
            "close": 140.0,
            "volume": 12345.0,
            "turnover_rate": 1.5,
        },
    )

    daily_snapshot_task()

    with get_db() as session:
        row = session.get(StockYearRange, "sh600519")
        assert row is not None
        assert row.year_low == 80.0
        assert row.year_high == 200.0
        assert row.current_price == 140.0
        assert row.water_level == 0.5


def test_startup_catchup_refreshes_year_range_on_weekend(tmp_path, monkeypatch) -> None:
    sqlite_path = tmp_path / "amtsm.db"
    monkeypatch.setattr(settings, "sqlite_path", str(sqlite_path))
    init_db()
    runtime_state.reset_daily()
    add_watchlist(WatchlistCreate(stock_code="600519", stock_name="贵州茅台"))

    with get_db() as session:
        session.add(
            DailyMarketSnapshot(
                stock_code="sh600519",
                trade_date="2026-08-14",
                open_price=90.0,
                high_price=200.0,
                low_price=80.0,
                close_price=140.0,
                volume=1000.0,
            )
        )
        session.commit()

    fake_now = datetime(2026, 8, 16, 10, 0, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    monkeypatch.setattr("app.engine.tasks.datetime", _FrozenDateTime(fake_now))

    startup_market_data_catchup()

    with get_db() as session:
        row = session.get(StockYearRange, "sh600519")
        assert row is not None
        assert row.year_low == 80.0
        assert row.year_high == 200.0
        assert row.current_price == 140.0
        assert abs(row.water_level - 0.5) < 1e-9
