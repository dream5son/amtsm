from copy import deepcopy
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from app.config import settings
from app.db.connection import get_db
from app.db.init_db import init_db
from app.db.models import Watchlist
from app.engine.state import runtime_state
from app.engine.tasks import baseline_precompute_task, market_polling_task
from app.market_signal.builtin_recipes import (
    PEAK_VALUE_WITH_VOLUME_RECIPE,
    rewrite_recipe_params,
)
from app.market_signal.recipe_schema import normalize
from app.services.signal_strategy_service import (
    assign_watchlist_strategy,
    create_strategy,
)


class _FrozenDateTime:
    def __init__(self, now: datetime) -> None:
        self._now = now

    def now(self, tz=None) -> datetime:
        if tz is None:
            return self._now
        return self._now.astimezone(tz)


def _add_stocks() -> None:
    with get_db() as session:
        session.add_all(
            [
                Watchlist(stock_code="sh600519", stock_name="贵州茅台"),
                Watchlist(stock_code="sz000001", stock_name="平安银行"),
            ]
        )
        session.commit()


def test_baseline_precompute_uses_each_effective_recipe_lookback(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(settings, "sqlite_path", str(tmp_path / "amtsm.db"))
    init_db()
    runtime_state.reset_daily()
    _add_stocks()

    short = create_strategy(
        "短周期",
        None,
        rewrite_recipe_params(
            PEAK_VALUE_WITH_VOLUME_RECIPE,
            lookback_days=20,
            volume_lookback=3,
        ),
    )
    long = create_strategy(
        "长周期",
        None,
        rewrite_recipe_params(
            PEAK_VALUE_WITH_VOLUME_RECIPE,
            lookback_days=40,
            volume_lookback=9,
        ),
    )
    assign_watchlist_strategy("sh600519", short["id"])
    assign_watchlist_strategy("sz000001", long["id"])

    calls = {}

    def fake_fetch(code: str, count: int):
        calls[code] = count
        start = date(2026, 1, 1)
        return [
            {
                "date": (start + timedelta(days=index)).isoformat(),
                "open": 10.0,
                "high": 10.2,
                "low": 9.8,
                "close": 10.0,
                "volume": 1000.0,
            }
            for index in range(count)
        ]

    monkeypatch.setattr("app.engine.tasks.fetch_daily_bars", fake_fetch)
    baseline_precompute_task()

    assert calls == {"sh600519": 20, "sz000001": 40}
    assert runtime_state.baseline_cache["sh600519"]["price_lookback"] == 20
    assert runtime_state.baseline_cache["sh600519"]["volume_lookback"] == 3
    assert runtime_state.baseline_cache["sh600519"]["strategy_id"] == short["id"]
    assert runtime_state.baseline_cache["sz000001"]["price_lookback"] == 40
    assert runtime_state.baseline_cache["sz000001"]["volume_lookback"] == 9
    assert runtime_state.baseline_cache["sz000001"]["strategy_id"] == long["id"]


def test_market_polling_evaluates_each_stocks_resolved_recipe(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(settings, "sqlite_path", str(tmp_path / "amtsm.db"))
    init_db()
    runtime_state.reset_daily()
    _add_stocks()

    base = normalize(PEAK_VALUE_WITH_VOLUME_RECIPE)
    strict_recipe = deepcopy(base)
    permissive_recipe = deepcopy(base)
    for channel in ("buy", "addon"):
        strict_recipe["channels"][channel]["all"][0]["params"]["factor"] = 1.05
        permissive_recipe["channels"][channel]["all"][0]["params"]["factor"] = 1.15
    strict = create_strategy("严格买入", None, strict_recipe)
    permissive = create_strategy("宽松买入", None, permissive_recipe)
    assign_watchlist_strategy("sh600519", strict["id"])
    assign_watchlist_strategy("sz000001", permissive["id"])

    fake_now = datetime(2026, 8, 5, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    trade_date = fake_now.date().isoformat()
    for code, strategy in (
        ("sh600519", strict),
        ("sz000001", permissive),
    ):
        runtime_state.baseline_cache[code] = {
            "low_min": 100.0,
            "high_max": 130.0,
            "actual_n": 60,
            "trade_date": trade_date,
            "avg_volume": 1000.0,
            "volume_lookback_n": 7,
            "volume_lookback": 7,
            "recipe_hash": strategy["recipe_hash"],
            "strategy_id": strategy["id"],
        }
    runtime_state.last_intraday_snapshot_at = fake_now

    monkeypatch.setattr("app.engine.tasks.datetime", _FrozenDateTime(fake_now))
    monkeypatch.setattr(
        "app.engine.tasks.fetch_realtime_quotes_batch",
        lambda codes, **kwargs: {
            code: {
                "price": 110.0,
                "prev_close": 110.0,
                "volume": 1000.0,
                "quote_date": trade_date,
                "is_halted": False,
                "has_quote": True,
            }
            for code in codes
        },
    )
    captured = []
    monkeypatch.setattr(
        "app.engine.tasks.process_buy_candidates",
        lambda candidates: captured.extend(candidates),
    )
    monkeypatch.setattr(
        "app.engine.tasks.process_sell_candidates", lambda candidates: []
    )
    monkeypatch.setattr(
        "app.engine.tasks.process_risk_candidates", lambda candidates: []
    )

    market_polling_task()

    assert "sh600519" not in runtime_state.signal_state
    assert runtime_state.signal_state["sz000001"] == "BUY"
    assert [item["stock_code"] for item in captured] == ["sz000001"]
    assert captured[0]["used_coeff"] == 1.15
