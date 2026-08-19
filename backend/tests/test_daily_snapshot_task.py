from datetime import date
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from app.config import settings
from app.db.connection import get_db
from app.db.init_db import init_db
from app.db.models import DailyMarketSnapshot
from app.engine.tasks import daily_snapshot_task, get_snapshot, upsert_snapshot
from app.schemas.watchlist import WatchlistCreate
from app.services.market_data.akshare_provider import _normalize_df_em
from app.services.market_data_service import (
    MarketDataUnavailableError,
    MissingTradeDayBarError,
    StockDataFetchError,
    fetch_trade_day_bar,
)
from app.services.watchlist_service import add_watchlist


def test_normalize_df_em_includes_turnover_rate() -> None:
    df = pd.DataFrame(
        [
            {
                "日期": "2026-08-05",
                "开盘": 10.0,
                "最高": 11.0,
                "最低": 9.5,
                "收盘": 10.5,
                "成交量": 100000,
                "换手率": 1.23,
            }
        ]
    )
    bars = _normalize_df_em(df)
    assert bars[0]["turnover_rate"] == pytest.approx(1.23)
    assert bars[0]["date"] == "2026-08-05"


def test_fetch_trade_day_bar_returns_matching_day() -> None:
    fake_bars = [
        {
            "date": "2026-08-05",
            "open": 10.0,
            "high": 11.0,
            "low": 9.5,
            "close": 10.5,
            "volume": 1000.0,
            "turnover_rate": 2.5,
        }
    ]
    provider = MagicMock()
    provider.fetch_daily_ohlcv.return_value = fake_bars
    with patch(
        "app.services.market_data_service.get_market_data_provider",
        return_value=provider,
    ):
        bar = fetch_trade_day_bar("sh600519", date(2026, 8, 5), retries=0)

    assert bar["close"] == 10.5
    assert bar["turnover_rate"] == 2.5
    provider.fetch_daily_ohlcv.assert_called_once_with(
        "sh600519", date(2026, 8, 5), date(2026, 8, 5), adjust="qfq"
    )


def test_fetch_trade_day_bar_missing_day_raises() -> None:
    provider = MagicMock()
    provider.fetch_daily_ohlcv.return_value = [
        {
            "date": "2026-08-04",
            "open": 10.0,
            "high": 11.0,
            "low": 9.5,
            "close": 10.5,
            "volume": 1000.0,
            "turnover_rate": None,
        }
    ]
    with (
        patch(
            "app.services.market_data_service.get_market_data_provider",
            return_value=provider,
        ),
        pytest.raises(MissingTradeDayBarError),
    ):
        fetch_trade_day_bar("sh600519", date(2026, 8, 5), retries=0)


def test_upsert_snapshot_is_idempotent(tmp_path, monkeypatch) -> None:
    sqlite_path = tmp_path / "amtsm.db"
    monkeypatch.setattr(settings, "sqlite_path", str(sqlite_path))
    init_db()

    upsert_snapshot("sh600519", "2026-08-05", 10, 12, 9, 11, 1000, 1.1)
    upsert_snapshot("sh600519", "2026-08-05", 10.5, 12.5, 9.5, 11.5, 2000, 2.2)

    with get_db() as session:
        rows = session.query(DailyMarketSnapshot).filter_by(stock_code="sh600519").all()
    assert len(rows) == 1
    snap = get_snapshot("sh600519", "2026-08-05")
    assert snap is not None
    assert snap["close_price"] == 11.5
    assert snap["volume"] == 2000
    assert snap["turnover_rate"] == 2.2


def test_daily_snapshot_task_skips_non_trading_day(monkeypatch) -> None:
    from datetime import datetime
    from zoneinfo import ZoneInfo

    fake_now = datetime(2026, 8, 8, 15, 30, tzinfo=ZoneInfo("Asia/Shanghai"))  # Saturday

    class _FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fake_now if tz is None else fake_now.astimezone(tz)

    monkeypatch.setattr("app.engine.tasks.datetime", _FrozenDateTime)
    with patch("app.engine.tasks.fetch_trade_day_bar") as mock_fetch:
        daily_snapshot_task()
    mock_fetch.assert_not_called()


def test_daily_snapshot_task_writes_all_normal_stocks(tmp_path, monkeypatch) -> None:
    from datetime import datetime
    from zoneinfo import ZoneInfo

    sqlite_path = tmp_path / "amtsm.db"
    monkeypatch.setattr(settings, "sqlite_path", str(sqlite_path))
    monkeypatch.setattr(settings, "snapshot_fetch_retries", 0)
    monkeypatch.setattr(settings, "snapshot_fetch_retry_backoff_seconds", 0.0)
    init_db()
    add_watchlist(WatchlistCreate(stock_code="600519", stock_name="贵州茅台"))
    add_watchlist(WatchlistCreate(stock_code="000001", stock_name="平安银行"))

    fake_now = datetime(2026, 8, 5, 15, 30, tzinfo=ZoneInfo("Asia/Shanghai"))

    class _FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fake_now if tz is None else fake_now.astimezone(tz)

    def fake_bar(code, trade_date, **kwargs):
        base = 100.0 if code == "sh600519" else 10.0
        return {
            "date": trade_date.isoformat(),
            "open": base,
            "high": base + 1,
            "low": base - 1,
            "close": base + 0.5,
            "volume": 12345.0,
            "turnover_rate": 1.5,
        }

    monkeypatch.setattr("app.engine.tasks.datetime", _FrozenDateTime)
    monkeypatch.setattr("app.engine.tasks.fetch_trade_day_bar", fake_bar)

    daily_snapshot_task()

    assert get_snapshot("sh600519", "2026-08-05")["close_price"] == 100.5
    assert get_snapshot("sz000001", "2026-08-05")["close_price"] == 10.5


def test_daily_snapshot_task_skips_missing_and_continues(tmp_path, monkeypatch) -> None:
    from datetime import datetime
    from zoneinfo import ZoneInfo

    sqlite_path = tmp_path / "amtsm.db"
    monkeypatch.setattr(settings, "sqlite_path", str(sqlite_path))
    monkeypatch.setattr(settings, "snapshot_fetch_retries", 0)
    init_db()
    add_watchlist(WatchlistCreate(stock_code="600519", stock_name="贵州茅台"))
    add_watchlist(WatchlistCreate(stock_code="000001", stock_name="平安银行"))

    fake_now = datetime(2026, 8, 5, 15, 30, tzinfo=ZoneInfo("Asia/Shanghai"))

    class _FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fake_now if tz is None else fake_now.astimezone(tz)

    def fake_bar(code, trade_date, **kwargs):
        if code == "sh600519":
            raise MissingTradeDayBarError("no bar")
        return {
            "date": trade_date.isoformat(),
            "open": 10.0,
            "high": 11.0,
            "low": 9.0,
            "close": 10.5,
            "volume": 100.0,
            "turnover_rate": None,
        }

    monkeypatch.setattr("app.engine.tasks.datetime", _FrozenDateTime)
    monkeypatch.setattr("app.engine.tasks.fetch_trade_day_bar", fake_bar)

    daily_snapshot_task()

    assert get_snapshot("sh600519", "2026-08-05") is None
    assert get_snapshot("sz000001", "2026-08-05")["close_price"] == 10.5


def test_daily_snapshot_task_upsert_idempotent(tmp_path, monkeypatch) -> None:
    from datetime import datetime
    from zoneinfo import ZoneInfo

    sqlite_path = tmp_path / "amtsm.db"
    monkeypatch.setattr(settings, "sqlite_path", str(sqlite_path))
    monkeypatch.setattr(settings, "snapshot_fetch_retries", 0)
    init_db()
    add_watchlist(WatchlistCreate(stock_code="600519", stock_name="贵州茅台"))

    fake_now = datetime(2026, 8, 5, 15, 30, tzinfo=ZoneInfo("Asia/Shanghai"))

    class _FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fake_now if tz is None else fake_now.astimezone(tz)

    call_count = {"n": 0}

    def fake_bar(code, trade_date, **kwargs):
        call_count["n"] += 1
        close = 100.0 + call_count["n"]
        return {
            "date": trade_date.isoformat(),
            "open": 100.0,
            "high": 110.0,
            "low": 90.0,
            "close": close,
            "volume": 1000.0,
            "turnover_rate": 1.0,
        }

    monkeypatch.setattr("app.engine.tasks.datetime", _FrozenDateTime)
    monkeypatch.setattr("app.engine.tasks.fetch_trade_day_bar", fake_bar)

    daily_snapshot_task()
    daily_snapshot_task()

    with get_db() as session:
        count = session.query(DailyMarketSnapshot).filter_by(stock_code="sh600519").count()
    assert count == 1
    assert get_snapshot("sh600519", "2026-08-05")["close_price"] == 102.0


def test_daily_snapshot_task_continues_on_stock_fetch_error(tmp_path, monkeypatch) -> None:
    from datetime import datetime
    from zoneinfo import ZoneInfo

    sqlite_path = tmp_path / "amtsm.db"
    monkeypatch.setattr(settings, "sqlite_path", str(sqlite_path))
    monkeypatch.setattr(settings, "snapshot_fetch_retries", 0)
    init_db()
    add_watchlist(WatchlistCreate(stock_code="600519", stock_name="贵州茅台"))
    add_watchlist(WatchlistCreate(stock_code="000001", stock_name="平安银行"))

    fake_now = datetime(2026, 8, 5, 15, 30, tzinfo=ZoneInfo("Asia/Shanghai"))

    class _FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fake_now if tz is None else fake_now.astimezone(tz)

    def fake_bar(code, trade_date, **kwargs):
        if code == "sh600519":
            raise StockDataFetchError("timeout")
        return {
            "date": trade_date.isoformat(),
            "open": 10.0,
            "high": 11.0,
            "low": 9.0,
            "close": 10.5,
            "volume": 100.0,
            "turnover_rate": 0.8,
        }

    monkeypatch.setattr("app.engine.tasks.datetime", _FrozenDateTime)
    monkeypatch.setattr("app.engine.tasks.fetch_trade_day_bar", fake_bar)

    daily_snapshot_task()
    assert get_snapshot("sz000001", "2026-08-05") is not None


def test_daily_snapshot_task_aborts_on_global_unavailable(tmp_path, monkeypatch) -> None:
    from datetime import datetime
    from zoneinfo import ZoneInfo

    sqlite_path = tmp_path / "amtsm.db"
    monkeypatch.setattr(settings, "sqlite_path", str(sqlite_path))
    monkeypatch.setattr(settings, "snapshot_fetch_retries", 0)
    init_db()
    add_watchlist(WatchlistCreate(stock_code="600519", stock_name="贵州茅台"))
    add_watchlist(WatchlistCreate(stock_code="000001", stock_name="平安银行"))

    fake_now = datetime(2026, 8, 5, 15, 30, tzinfo=ZoneInfo("Asia/Shanghai"))

    class _FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fake_now if tz is None else fake_now.astimezone(tz)

    call_count = {"n": 0}

    def fake_bar(code, trade_date, **kwargs):
        call_count["n"] += 1
        raise MarketDataUnavailableError("all down")

    monkeypatch.setattr("app.engine.tasks.datetime", _FrozenDateTime)
    monkeypatch.setattr("app.engine.tasks.fetch_trade_day_bar", fake_bar)

    daily_snapshot_task()
    assert call_count["n"] == 1
    assert get_snapshot("sh600519", "2026-08-05") is None
    assert get_snapshot("sz000001", "2026-08-05") is None
