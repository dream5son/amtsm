from __future__ import annotations

from copy import deepcopy
from datetime import date, timedelta

import pytest
from sqlalchemy import text

from app.config import settings
from app.db.connection import get_db
from app.db.init_db import init_db
from app.db.models import (
    DailyMarketSnapshot,
    SignalStrategy,
    StockStrategyOverride,
    StrategyConfig,
    Watchlist,
)
from app.market_signal.builtin_recipes import PEAK_VALLEY_RECIPE
from app.market_signal.recipe_schema import normalize
from app.services.signal_strategy_service import (
    archive_strategy,
    assign_watchlist_strategy,
    clone_strategy,
    create_strategy,
    dry_run,
    get_strategy,
    list_strategies,
    resolve_signal_strategy,
    set_default_strategy,
    update_strategy,
)


@pytest.fixture
def signal_db(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "sqlite_path", str(tmp_path / "amtsm.db"))
    init_db()


def test_init_seeds_builtins_and_default_pointer(signal_db) -> None:
    strategies = list_strategies()
    builtins = {row["builtin_key"]: row for row in strategies if row["builtin_key"]}

    assert set(builtins) == {"peak_valley", "peak_value_with_volume"}
    assert len(strategies) == 2
    with get_db() as session:
        config = session.get(StrategyConfig, 1)
        assert config is not None
        assert (
            config.default_signal_strategy_id
            == builtins["peak_value_with_volume"]["id"]
        )

    init_db()
    assert len(list_strategies()) == 2


def test_create_update_clone_and_archive_rules(signal_db) -> None:
    recipe = normalize(PEAK_VALLEY_RECIPE)
    created = create_strategy("自定义策略", "initial", recipe)
    updated_recipe = deepcopy(recipe)
    for channel in ("buy", "addon"):
        updated_recipe["channels"][channel]["all"][0]["params"]["factor"] = 1.05

    updated = update_strategy(
        created["id"],
        expected_version=1,
        name="自定义策略 2",
        description=None,
        recipe=updated_recipe,
    )

    assert updated["recipe_version"] == 2
    assert updated["description"] is None
    assert updated["recipe_hash"] != created["recipe_hash"]
    with pytest.raises(ValueError, match="version conflict"):
        update_strategy(created["id"], expected_version=1, name="stale")

    cloned = clone_strategy(created["id"], "克隆策略")
    assert cloned["builtin_key"] is None
    assert cloned["recipe_hash"] == updated["recipe_hash"]
    archived = archive_strategy(cloned["id"])
    assert archived["is_archived"] is True
    assert cloned["id"] not in {row["id"] for row in list_strategies()}
    assert cloned["id"] in {
        row["id"] for row in list_strategies(include_archived=True)
    }

    builtin = next(row for row in list_strategies() if row["builtin_key"])
    with pytest.raises(ValueError, match="immutable"):
        update_strategy(builtin["id"], expected_version=1, name="nope")
    with pytest.raises(ValueError, match="cannot be archived"):
        archive_strategy(builtin["id"])


def test_assignment_inherits_default_and_overrides(signal_db) -> None:
    with get_db() as session:
        session.add(Watchlist(stock_code="sh600519", stock_name="贵州茅台"))
        session.commit()

    inherited = resolve_signal_strategy("600519")
    assert inherited["builtin_key"] == "peak_value_with_volume"

    custom = create_strategy("个股策略", None, normalize(PEAK_VALLEY_RECIPE))
    assigned = assign_watchlist_strategy("600519", custom["id"])
    assert assigned["id"] == custom["id"]
    with pytest.raises(ValueError, match="assigned"):
        archive_strategy(custom["id"])

    inherited_again = assign_watchlist_strategy("600519", None)
    assert inherited_again["id"] == inherited["id"]
    set_default_strategy(custom["id"])
    assert resolve_signal_strategy("600519")["id"] == custom["id"]
    with pytest.raises(ValueError, match="default"):
        archive_strategy(custom["id"])


def test_dry_run_returns_decision_for_seeded_snapshots(signal_db) -> None:
    stock_code = "sh600519"
    start = date(2026, 1, 1)
    with get_db() as session:
        session.add(Watchlist(stock_code=stock_code, stock_name="贵州茅台"))
        for index in range(61):
            is_current = index == 60
            session.add(
                DailyMarketSnapshot(
                    stock_code=stock_code,
                    trade_date=(start + timedelta(days=index)).isoformat(),
                    open_price=104.0 if is_current else 105.0,
                    high_price=106.0 if is_current else 120.0,
                    low_price=104.0 if is_current else 100.0,
                    close_price=105.0,
                    volume=1000.0,
                )
            )
        session.commit()
        strategy_id = (
            session.query(SignalStrategy)
            .filter_by(builtin_key="peak_value_with_volume")
            .one()
            .id
        )

    result = dry_run(stock_code, strategy_id=strategy_id)

    assert result["stock_code"] == stock_code
    assert result["decision"]["buy"] is True
    assert set(result["traces"]) == {"buy", "sell", "addon"}
    assert result["requirements"] == {
        "price_lookback_days": 60,
        "volume_lookback_days": 7,
        "requires_current_volume": True,
    }


def test_migration_groups_effective_custom_params(signal_db) -> None:
    with get_db() as session:
        session.add_all(
            [
                Watchlist(
                    stock_code="sh600519",
                    stock_name="贵州茅台",
                    custom_n=30,
                    custom_x=1.05,
                    custom_y=0.85,
                ),
                Watchlist(stock_code="sz000001", stock_name="平安银行"),
            ]
        )
        session.add(
            StockStrategyOverride(
                stock_code="sz000001",
                custom_n=30,
                custom_x=1.05,
                custom_y=0.85,
            )
        )
        session.execute(
            text("DELETE FROM schema_meta WHERE key = 'signal_strategies_v1'")
        )
        session.commit()

    init_db()

    with get_db() as session:
        watchlists = session.query(Watchlist).order_by(Watchlist.stock_code).all()
        strategy_ids = {row.signal_strategy_id for row in watchlists}
        assert None not in strategy_ids
        assert len(strategy_ids) == 1
        strategy = session.get(SignalStrategy, strategy_ids.pop())
        assert strategy is not None
        assert strategy.name == "迁移 · 30/1.05/0.85"

    migrated = get_strategy(strategy.id)
    buy_params = migrated["recipe"]["channels"]["buy"]["all"][0]["params"]
    sell_params = migrated["recipe"]["channels"]["sell"]["all"][0]["params"]
    assert buy_params == {"lookback_days": 30, "factor": 1.05}
    assert sell_params == {"lookback_days": 30, "factor": 0.85}
