import json

from app.config import settings
from app.db.connection import get_db
from app.db.init_db import init_db
from app.db.models import BacktestJob, DailyMarketSnapshot, Watchlist
from app.schemas.strategy import DEFAULT_TRAILING_LADDER
from app.services.backtest_service import (
    BacktestApplyError,
    apply_job_params,
    get_kline_data,
)
from app.services.strategy_service import get_override, get_strategy


_SAMPLE_PARAMS = {
    "n": 30,
    "x": 1.05,
    "y": 0.95,
    "stop_loss_pct": 0.06,
    "break_even_trigger_pct": 0.12,
    "break_even_buffer_pct": 0.008,
    "trailing_ladder": DEFAULT_TRAILING_LADDER,
}


def _seed_job(
    session,
    *,
    stock_code: str,
    start_date: str,
    end_date: str,
    status: str = "SUCCESS",
    params: dict | None = None,
) -> int:
    job = BacktestJob(
        stock_code=stock_code,
        status=status,
        start_date=start_date,
        end_date=end_date,
        params_json=json.dumps(params if params is not None else {}, separators=(",", ":")),
        params_hash="h",
        compare_group_id="g",
    )
    session.add(job)
    session.commit()
    return job.id


def test_get_kline_data_drops_unsanitary_ohlc_bars(tmp_path, monkeypatch) -> None:
    """Mirrors run_backtest's sanitize_bars_for_backtest: negative qfq, zero
    halt bars, and extreme-range lows must never appear on the chart if the
    simulation excluded them. Large day-to-day moves are kept."""
    sqlite_path = tmp_path / "amtsm.db"
    monkeypatch.setattr(settings, "sqlite_path", str(sqlite_path))
    init_db()

    with get_db() as session:
        job_id = _seed_job(session, stock_code="sh600660", start_date="2000-01-01", end_date="2000-01-12")
        session.add_all(
            [
                DailyMarketSnapshot(
                    stock_code="sh600660",
                    trade_date="2000-01-04",
                    open_price=-14.71,
                    high_price=-14.66,
                    low_price=-14.71,
                    close_price=-14.66,
                    volume=1345859.0,
                ),
                DailyMarketSnapshot(
                    stock_code="sh600660",
                    trade_date="2000-01-05",
                    open_price=0.0,
                    high_price=0.0,
                    low_price=0.0,
                    close_price=0.0,
                    volume=0.0,
                ),
                DailyMarketSnapshot(
                    stock_code="sh600660",
                    trade_date="2000-01-06",
                    open_price=10.0,
                    high_price=10.5,
                    low_price=9.8,
                    close_price=10.2,
                    volume=6013593.0,
                ),
                DailyMarketSnapshot(
                    stock_code="sh600660",
                    trade_date="2000-01-07",
                    open_price=0.51,
                    high_price=1.01,
                    low_price=0.02,
                    close_price=0.31,
                    volume=100.0,
                ),
                DailyMarketSnapshot(
                    stock_code="sh600660",
                    trade_date="2000-01-10",
                    open_price=12.5,
                    high_price=12.8,
                    low_price=12.4,
                    close_price=12.6,
                    volume=100.0,
                ),
                DailyMarketSnapshot(
                    stock_code="sh600660",
                    trade_date="2000-01-11",
                    open_price=10.1,
                    high_price=10.3,
                    low_price=10.0,
                    close_price=10.2,
                    volume=100.0,
                ),
            ]
        )
        session.commit()

    result = get_kline_data(job_id)

    # Negative/zero/extreme-range dropped; 01-10 large move and 01-11 kept.
    assert [bar["trade_date"] for bar in result["bars"]] == [
        "2000-01-06",
        "2000-01-10",
        "2000-01-11",
    ]
    assert all(bar["open_price"] > 0 for bar in result["bars"])


def test_apply_job_params_writes_stock_override_only(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "sqlite_path", str(tmp_path / "amtsm.db"))
    init_db()

    with get_db() as session:
        session.add(Watchlist(stock_code="sh600519", stock_name="茅台", status="NORMAL"))
        job_id = _seed_job(
            session,
            stock_code="sh600519",
            start_date="2020-01-01",
            end_date="2024-01-01",
            params=_SAMPLE_PARAMS,
        )

    before_global = get_strategy()
    result = apply_job_params(job_id, update_global=False)

    assert result["stock_code"] == "sh600519"
    assert result["updated_global"] is False
    assert result["global_strategy"] is None
    override = result["override"]
    assert override["custom_n"] == 30
    assert abs(override["custom_x"] - 1.05) < 1e-9
    assert abs(override["custom_y"] - 0.95) < 1e-9
    assert abs(override["stop_loss_pct"] - 0.06) < 1e-9
    assert abs(override["break_even_trigger_pct"] - 0.12) < 1e-9

    after_global = get_strategy()
    assert after_global["global_buy_n"] == before_global["global_buy_n"]
    assert abs(after_global["stop_loss_pct"] - before_global["stop_loss_pct"]) < 1e-9


def test_apply_job_params_update_global(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "sqlite_path", str(tmp_path / "amtsm.db"))
    init_db()

    with get_db() as session:
        session.add(Watchlist(stock_code="sz000001", stock_name="平安", status="NORMAL"))
        job_id = _seed_job(
            session,
            stock_code="sz000001",
            start_date="2020-01-01",
            end_date="2024-01-01",
            params=_SAMPLE_PARAMS,
        )

    result = apply_job_params(job_id, update_global=True)
    assert result["updated_global"] is True
    global_cfg = result["global_strategy"]
    assert global_cfg is not None
    assert global_cfg["global_buy_n"] == 30
    assert global_cfg["global_sell_n"] == 30
    assert abs(global_cfg["global_buy_x"] - 1.05) < 1e-9
    assert abs(global_cfg["global_sell_y"] - 0.95) < 1e-9
    assert abs(global_cfg["stop_loss_pct"] - 0.06) < 1e-9
    assert get_override("sz000001")["custom_n"] == 30


def test_apply_job_params_rejects_non_success(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "sqlite_path", str(tmp_path / "amtsm.db"))
    init_db()

    with get_db() as session:
        session.add(Watchlist(stock_code="sh600519", stock_name="茅台", status="NORMAL"))
        job_id = _seed_job(
            session,
            stock_code="sh600519",
            start_date="2020-01-01",
            end_date="2024-01-01",
            status="FAILED",
            params=_SAMPLE_PARAMS,
        )

    try:
        apply_job_params(job_id)
        assert False, "expected BacktestApplyError"
    except BacktestApplyError as exc:
        assert "SUCCESS" in str(exc)


def test_apply_job_params_idempotent(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "sqlite_path", str(tmp_path / "amtsm.db"))
    init_db()

    with get_db() as session:
        session.add(Watchlist(stock_code="sh600519", stock_name="茅台", status="NORMAL"))
        job_id = _seed_job(
            session,
            stock_code="sh600519",
            start_date="2020-01-01",
            end_date="2024-01-01",
            params=_SAMPLE_PARAMS,
        )

    first = apply_job_params(job_id)
    second = apply_job_params(job_id)
    assert first["override"]["custom_n"] == second["override"]["custom_n"]
    assert abs(first["override"]["stop_loss_pct"] - second["override"]["stop_loss_pct"]) < 1e-9
    assert first["override"]["trailing_ladder"] == second["override"]["trailing_ladder"]
