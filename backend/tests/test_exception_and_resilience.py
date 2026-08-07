"""User story 12: exception handling and resilience."""

from datetime import datetime
from zoneinfo import ZoneInfo

import httpx
import pytest

from app.config import settings
from app.db.connection import get_db
from app.db.init_db import init_db
from app.db.models import DailyBaseline, StrategyConfig, Watchlist
from app.services.alert_service import get_alert
from app.engine.baseline_calculator import compute_baseline
from app.engine.resilience import (
    SYSTEM_ALERT_SIGNAL_TYPE,
    SYSTEM_ALERT_STOCK_CODE,
    get_system_status,
    record_poll_round_failure,
    record_poll_round_success,
)
from app.engine.state import runtime_state
from app.engine.tasks import baseline_precompute_task, market_polling_task
from app.schemas.watchlist import WatchlistCreate
from app.services.market_data_service import (
    MarketDataUnavailableError,
    _parse_sina_realtime_quotes,
    fetch_daily_bars,
    fetch_realtime_quotes_batch,
)
from app.services.watchlist_service import add_watchlist, list_watchlist


class _FrozenDateTime:
    def __init__(self, now: datetime) -> None:
        self._now = now

    def now(self, tz=None) -> datetime:
        if tz is None:
            return self._now
        return self._now.astimezone(tz)


def _reset_runtime() -> None:
    runtime_state.reset_daily()
    runtime_state.consecutive_poll_failures = 0
    runtime_state.quote_delay = False
    runtime_state.quote_delay_since = None
    runtime_state.last_internal_alert_key = None
    runtime_state.job_status = "IDLE"


def test_qfq_used_for_ex_dividend_alignment(monkeypatch) -> None:
    """Ex-dividend safety: daily bars must request forward adjustment (qfq)."""
    captured: dict = {}

    def fake_ha(numeric_code, start_date, end_date, adjust="qfq"):
        captured["adjust"] = adjust
        captured["code"] = numeric_code
        return [
            {
                "date": "2026-08-04",
                "open": 10.0,
                "high": 11.0,
                "low": 9.0,
                "close": 10.5,
                "volume": 1000,
            }
        ]

    monkeypatch.setattr(
        "app.services.market_data_service.high_available_akshare",
        fake_ha,
    )
    bars = fetch_daily_bars("sh600519", 60)
    assert captured["adjust"] == "qfq"
    assert len(bars) == 1
    low_min, high_max, actual_n = compute_baseline(bars, 60)
    assert actual_n == 1
    assert low_min == 9.0
    assert high_max == 11.0


def test_insufficient_days_exposed_on_watchlist(tmp_path, monkeypatch) -> None:
    sqlite_path = tmp_path / "amtsm.db"
    monkeypatch.setattr(settings, "sqlite_path", str(sqlite_path))
    init_db()
    _reset_runtime()
    add_watchlist(WatchlistCreate(stock_code="600519", stock_name="贵州茅台"))

    with get_db() as session:
        session.add(
            DailyBaseline(
                stock_code="sh600519",
                trade_date="2026-08-05",
                low_min=10.0,
                high_max=12.0,
                actual_n=10,
            )
        )
        session.commit()

    items = list_watchlist()
    assert items[0]["actual_n"] == 10
    assert items[0]["effective_n"] == 60
    assert items[0]["insufficient_days"] == 50


def test_realtime_retry_exponential_backoff(monkeypatch) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr(
        "app.services.market_data_service.time.sleep",
        lambda seconds: sleeps.append(seconds),
    )

    calls = {"n": 0}

    def fake_get(*args, **kwargs):
        calls["n"] += 1
        raise httpx.TimeoutException("timed out")

    monkeypatch.setattr("app.services.market_data_service.httpx.get", fake_get)

    with pytest.raises(MarketDataUnavailableError):
        fetch_realtime_quotes_batch(
            ["sh600519"],
            timeout_seconds=0.1,
            retries=3,
            retry_backoff_seconds=0.5,
        )

    assert calls["n"] == 4  # 1 initial + 3 retries
    assert sleeps == [0.5, 1.0, 2.0]


def test_quote_delay_after_consecutive_failures(tmp_path, monkeypatch) -> None:
    sqlite_path = tmp_path / "amtsm.db"
    monkeypatch.setattr(settings, "sqlite_path", str(sqlite_path))
    monkeypatch.setattr(settings, "polling_consecutive_failure_threshold", 5)
    init_db()
    _reset_runtime()

    for i in range(4):
        record_poll_round_failure(trade_date="2026-08-05", reason=f"fail-{i}")
        assert runtime_state.quote_delay is False

    record_poll_round_failure(trade_date="2026-08-05", reason="fail-4")
    assert runtime_state.quote_delay is True
    status = get_system_status()
    assert status["quote_delay"] is True
    assert status["consecutive_poll_failures"] == 5

    alert = get_alert(SYSTEM_ALERT_STOCK_CODE, "2026-08-05", SYSTEM_ALERT_SIGNAL_TYPE)
    assert alert is not None
    assert alert["sent_status"] == "INTERNAL"
    assert alert["error_code"] == "QUOTE_DELAY"

    record_poll_round_success()
    assert runtime_state.quote_delay is False
    assert runtime_state.consecutive_poll_failures == 0


def test_halt_persisted_and_excluded_from_polling(tmp_path, monkeypatch) -> None:
    sqlite_path = tmp_path / "amtsm.db"
    monkeypatch.setattr(settings, "sqlite_path", str(sqlite_path))
    monkeypatch.setattr(settings, "polling_batch_size", 50)
    monkeypatch.setattr(settings, "polling_request_retries", 0)
    monkeypatch.setattr(settings, "polling_request_retry_backoff_seconds", 0.0)

    init_db()
    _reset_runtime()
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
    monkeypatch.setattr("app.engine.tasks.process_sell_candidates", lambda candidates: [])
    monkeypatch.setattr(
        "app.engine.tasks.fetch_realtime_quotes_batch",
        lambda *args, **kwargs: {
            "sh600519": {
                "stock_name": "贵州茅台",
                "price": 0.0,
                "open": 0.0,
                "prev_close": 110.0,
                "quote_date": "2026-08-05",
                "quote_time": "10:00:00",
                "is_halted": True,
                "has_quote": True,
            }
        },
    )

    market_polling_task()

    with get_db() as session:
        status = session.query(Watchlist).filter_by(stock_code="sh600519").one().status
    assert status == "HALT"
    assert "sh600519" not in runtime_state.signal_state

    # Second round must not request quotes for HALT stocks.
    called = {"hit": False}

    def _should_not_call(*args, **kwargs):
        called["hit"] = True
        return {}

    monkeypatch.setattr("app.engine.tasks.fetch_realtime_quotes_batch", _should_not_call)
    market_polling_task()
    assert called["hit"] is False


def test_missing_quote_does_not_persist_halt(tmp_path, monkeypatch) -> None:
    sqlite_path = tmp_path / "amtsm.db"
    monkeypatch.setattr(settings, "sqlite_path", str(sqlite_path))
    monkeypatch.setattr(settings, "polling_request_retries", 0)
    monkeypatch.setattr(settings, "polling_request_retry_backoff_seconds", 0.0)
    init_db()
    _reset_runtime()
    add_watchlist(WatchlistCreate(stock_code="600519", stock_name="贵州茅台"))

    with get_db() as session:
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
    monkeypatch.setattr(
        "app.engine.tasks.fetch_realtime_quotes_batch",
        lambda *args, **kwargs: {
            "sh600519": {
                "stock_name": "sh600519",
                "price": None,
                "open": None,
                "prev_close": None,
                "quote_date": None,
                "quote_time": None,
                "is_halted": False,
                "has_quote": False,
            }
        },
    )

    market_polling_task()
    with get_db() as session:
        status = session.query(Watchlist).filter_by(stock_code="sh600519").one().status
    assert status == "NORMAL"


def test_baseline_restores_halt_stock(tmp_path, monkeypatch) -> None:
    sqlite_path = tmp_path / "amtsm.db"
    monkeypatch.setattr(settings, "sqlite_path", str(sqlite_path))
    init_db()
    _reset_runtime()
    add_watchlist(WatchlistCreate(stock_code="600519", stock_name="贵州茅台"))
    with get_db() as session:
        session.query(Watchlist).filter_by(stock_code="sh600519").update({"status": "HALT"})
        session.commit()

    bars = [
        {
            "date": f"2026-07-{i + 1:02d}",
            "open": 10.0,
            "high": 11.0,
            "low": 9.0,
            "close": 10.0,
            "volume": 1000,
        }
        for i in range(20)
    ]
    monkeypatch.setattr("app.engine.tasks.fetch_daily_bars", lambda code, n: bars)

    baseline_precompute_task()

    with get_db() as session:
        status = session.query(Watchlist).filter_by(stock_code="sh600519").one().status
        baseline = session.query(DailyBaseline).filter_by(stock_code="sh600519").one_or_none()
    assert status == "NORMAL"
    assert baseline is not None
    assert baseline.actual_n == 20


def test_poll_round_records_quote_delay_on_all_batch_failures(tmp_path, monkeypatch) -> None:
    sqlite_path = tmp_path / "amtsm.db"
    monkeypatch.setattr(settings, "sqlite_path", str(sqlite_path))
    monkeypatch.setattr(settings, "polling_consecutive_failure_threshold", 2)
    monkeypatch.setattr(settings, "polling_request_retries", 0)
    monkeypatch.setattr(settings, "polling_request_retry_backoff_seconds", 0.0)
    init_db()
    _reset_runtime()
    add_watchlist(WatchlistCreate(stock_code="600519", stock_name="贵州茅台"))

    with get_db() as session:
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

    def _fail(*args, **kwargs):
        raise MarketDataUnavailableError("timed out")

    monkeypatch.setattr("app.engine.tasks.fetch_realtime_quotes_batch", _fail)

    market_polling_task()
    assert runtime_state.consecutive_poll_failures == 1
    assert runtime_state.quote_delay is False

    market_polling_task()
    assert runtime_state.consecutive_poll_failures == 2
    assert runtime_state.quote_delay is True


def test_parse_halted_quote_has_quote_flag() -> None:
    raw = (
        'var hq_str_sh600000="浦发银行,0.00,10.00,0.00,0.00,0.00,0.00,0.00,0,0,'
        '0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,2026-08-05,10:05:01,00";\n'
    )
    data = _parse_sina_realtime_quotes(raw, ["sh600000"])
    assert data["sh600000"]["has_quote"] is True
    assert data["sh600000"]["is_halted"] is True


def test_system_status_endpoint() -> None:
    from app.api.health import system_status

    _reset_runtime()
    runtime_state.quote_delay = True
    runtime_state.consecutive_poll_failures = 5
    runtime_state.quote_delay_since = "2026-08-05T02:00:00+00:00"

    body = system_status()
    assert body["quote_delay"] is True
    assert body["consecutive_poll_failures"] == 5
    assert body["quote_delay_since"] == "2026-08-05T02:00:00+00:00"
    assert body["failure_threshold"] >= 1
