from datetime import datetime
from zoneinfo import ZoneInfo

from app.config import settings
from app.db.connection import get_db
from app.db.init_db import init_db
from app.db.models import DailyBaseline
from app.engine.state import runtime_state
from app.engine.tasks import startup_market_data_catchup, upsert_snapshot
from app.schemas.watchlist import WatchlistCreate
from app.services.watchlist_service import add_watchlist

SH_TZ = ZoneInfo("Asia/Shanghai")


class _FrozenDateTime:
    def __init__(self, now: datetime) -> None:
        self._now = now

    def now(self, tz=None) -> datetime:
        if tz is None:
            return self._now
        return self._now.astimezone(tz)


def _setup(tmp_path, monkeypatch, fake_now: datetime) -> dict[str, int]:
    sqlite_path = tmp_path / "amtsm.db"
    monkeypatch.setattr(settings, "sqlite_path", str(sqlite_path))
    init_db()
    runtime_state.reset_daily()
    monkeypatch.setattr("app.engine.tasks.datetime", _FrozenDateTime(fake_now))

    called = {"baseline": 0, "intraday": 0, "daily": 0}
    monkeypatch.setattr(
        "app.engine.tasks.baseline_precompute_task",
        lambda: called.__setitem__("baseline", called["baseline"] + 1),
    )
    monkeypatch.setattr(
        "app.engine.tasks.intraday_snapshot_task",
        lambda: called.__setitem__("intraday", called["intraday"] + 1),
    )
    monkeypatch.setattr(
        "app.engine.tasks.daily_snapshot_task",
        lambda: called.__setitem__("daily", called["daily"] + 1),
    )
    return called


def test_catchup_runs_baseline_and_intraday_during_session(tmp_path, monkeypatch) -> None:
    fake_now = datetime(2026, 8, 17, 10, 0, 0, tzinfo=SH_TZ)
    called = _setup(tmp_path, monkeypatch, fake_now)

    startup_market_data_catchup()

    assert called["baseline"] == 1
    assert called["intraday"] == 1
    assert called["daily"] == 0


def test_catchup_skips_baseline_when_already_present(tmp_path, monkeypatch) -> None:
    fake_now = datetime(2026, 8, 17, 10, 0, 0, tzinfo=SH_TZ)
    called = _setup(tmp_path, monkeypatch, fake_now)

    with get_db() as session:
        session.add(
            DailyBaseline(
                stock_code="sh600519",
                trade_date="2026-08-17",
                low_min=100.0,
                high_max=120.0,
                actual_n=60,
            )
        )
        session.commit()

    startup_market_data_catchup()

    assert called["baseline"] == 0
    assert called["intraday"] == 1
    assert called["daily"] == 0


def test_catchup_skips_before_baseline_cutoff(tmp_path, monkeypatch) -> None:
    fake_now = datetime(2026, 8, 17, 8, 0, 0, tzinfo=SH_TZ)
    called = _setup(tmp_path, monkeypatch, fake_now)

    startup_market_data_catchup()

    assert called["baseline"] == 0
    assert called["intraday"] == 0
    assert called["daily"] == 0


def test_catchup_skips_weekend(tmp_path, monkeypatch) -> None:
    fake_now = datetime(2026, 8, 16, 10, 0, 0, tzinfo=SH_TZ)  # Sunday
    called = _setup(tmp_path, monkeypatch, fake_now)

    startup_market_data_catchup()

    assert called == {"baseline": 0, "intraday": 0, "daily": 0}


def test_catchup_runs_daily_snapshot_after_close_if_missing(tmp_path, monkeypatch) -> None:
    fake_now = datetime(2026, 8, 17, 15, 45, 0, tzinfo=SH_TZ)
    called = _setup(tmp_path, monkeypatch, fake_now)
    add_watchlist(WatchlistCreate(stock_code="600519", stock_name="贵州茅台"))

    startup_market_data_catchup()

    assert called["baseline"] == 1
    assert called["intraday"] == 0
    assert called["daily"] == 1


def test_catchup_skips_daily_snapshot_when_present(tmp_path, monkeypatch) -> None:
    fake_now = datetime(2026, 8, 17, 15, 45, 0, tzinfo=SH_TZ)
    called = _setup(tmp_path, monkeypatch, fake_now)
    add_watchlist(WatchlistCreate(stock_code="600519", stock_name="贵州茅台"))
    upsert_snapshot(
        stock_code="sh600519",
        trade_date="2026-08-17",
        open_price=10.0,
        high_price=11.0,
        low_price=9.5,
        close_price=10.5,
        volume=1000.0,
    )

    startup_market_data_catchup()

    assert called["baseline"] == 1
    assert called["intraday"] == 0
    assert called["daily"] == 0
