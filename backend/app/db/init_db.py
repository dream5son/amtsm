from __future__ import annotations

import json
import logging
from datetime import datetime

from sqlalchemy import inspect, text
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.db.connection import enable_wal, get_db, get_engine
from app.db.models import (
    Base,
    SignalStrategy,
    StockStrategyOverride,
    StrategyConfig,
    Watchlist,
)
from app.market_signal.builtin_recipes import (
    BUILTIN_RECIPES,
    PEAK_VALUE_WITH_VOLUME_RECIPE,
    recipe_with_params,
)
from app.market_signal.recipe_schema import hash_recipe, normalize
from app.schemas.strategy import (
    DEFAULT_BREAK_EVEN_BUFFER_PCT,
    DEFAULT_BREAK_EVEN_TRIGGER_PCT,
    DEFAULT_STOP_LOSS_PCT,
    DEFAULT_TRAILING_LADDER_JSON,
    LEGACY_BREAK_EVEN_TRIGGER_PCT,
    LEGACY_STOP_LOSS_PCT,
    LEGACY_TRAILING_LADDER,
    parse_trailing_ladder,
)

logger = logging.getLogger(__name__)

_PRICE_ADJUST_META_KEY = "price_adjust"
# Bump when the snapshot cache basis changes (e.g. unadjusted → qfq) so stale
# OHLC rows are purged once and backtests refetch on the new basis.
_PRICE_ADJUST_QFQ = "qfq_v2"
_PRICE_ADJUST_MIGRATED_AT_KEY = "price_adjust_migrated_at"
_SIGNAL_STRATEGIES_META_KEY = "signal_strategies_v1"

_BUILTIN_NAMES = {
    "peak_valley": "峰谷策略",
    "peak_value_with_volume": "峰谷量价策略",
}


def _existing_columns(table_name: str) -> set[str]:
    insp = inspect(get_engine())
    if table_name not in insp.get_table_names():
        return set()
    return {col["name"] for col in insp.get_columns(table_name)}


def _ensure_strategy_columns() -> None:
    existing_columns = _existing_columns("strategy_config")
    if not existing_columns:
        return

    with get_engine().begin() as conn:
        if "global_buy_n" not in existing_columns:
            conn.execute(
                text("ALTER TABLE strategy_config ADD COLUMN global_buy_n INTEGER DEFAULT 60")
            )
        if "global_buy_x" not in existing_columns:
            conn.execute(
                text("ALTER TABLE strategy_config ADD COLUMN global_buy_x REAL DEFAULT 1.10")
            )
        if "global_sell_n" not in existing_columns:
            conn.execute(
                text("ALTER TABLE strategy_config ADD COLUMN global_sell_n INTEGER DEFAULT 60")
            )
        if "global_sell_y" not in existing_columns:
            conn.execute(
                text("ALTER TABLE strategy_config ADD COLUMN global_sell_y REAL DEFAULT 0.90")
            )

        legacy_n_expr = "global_n" if "global_n" in existing_columns else "NULL"
        legacy_x_expr = "global_x" if "global_x" in existing_columns else "NULL"
        legacy_y_expr = "global_y" if "global_y" in existing_columns else "NULL"

        conn.execute(
            text(
                f"""
                UPDATE strategy_config
                SET global_buy_n = COALESCE(global_buy_n, {legacy_n_expr}, 60),
                    global_buy_x = COALESCE(global_buy_x, {legacy_x_expr}, 1.10),
                    global_sell_n = COALESCE(global_sell_n, {legacy_n_expr}, 60),
                    global_sell_y = COALESCE(global_sell_y, {legacy_y_expr}, 0.90)
                """
            )
        )


def _ensure_signal_strategy_columns() -> None:
    table_columns = {
        "watchlist": _existing_columns("watchlist"),
        "strategy_config": _existing_columns("strategy_config"),
    }
    with get_engine().begin() as conn:
        if (
            table_columns["watchlist"]
            and "signal_strategy_id" not in table_columns["watchlist"]
        ):
            conn.execute(
                text(
                    "ALTER TABLE watchlist ADD COLUMN signal_strategy_id "
                    "INTEGER NULL REFERENCES signal_strategies(id)"
                )
            )
        if (
            table_columns["strategy_config"]
            and "default_signal_strategy_id" not in table_columns["strategy_config"]
        ):
            conn.execute(
                text(
                    "ALTER TABLE strategy_config ADD COLUMN default_signal_strategy_id "
                    "INTEGER NULL REFERENCES signal_strategies(id)"
                )
            )


def _recipe_json(recipe) -> str:
    return json.dumps(
        normalize(recipe),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _seed_builtin_signal_strategies() -> None:
    with get_db() as session:
        config = session.get(StrategyConfig, 1)
        if config is None:
            return
        lookback = max(int(config.global_buy_n), int(config.global_sell_n))
        parameterized = {
            "peak_valley": recipe_with_params(
                BUILTIN_RECIPES["peak_valley"],
                lookback_days=lookback,
                x=float(config.global_buy_x),
                y=float(config.global_sell_y),
            ),
            "peak_value_with_volume": recipe_with_params(
                BUILTIN_RECIPES["peak_value_with_volume"],
                lookback_days=lookback,
                x=float(config.global_buy_x),
                y=float(config.global_sell_y),
                volume_lookback=7,
                buy_vol=0.0,
                sell_vol=0.30,
            ),
        }
        rows: dict[str, SignalStrategy] = {}
        for builtin_key, recipe in parameterized.items():
            row = (
                session.query(SignalStrategy)
                .filter(SignalStrategy.builtin_key == builtin_key)
                .one_or_none()
            )
            if row is None:
                row = SignalStrategy(
                    name=_BUILTIN_NAMES[builtin_key],
                    description=None,
                    builtin_key=builtin_key,
                    recipe_json=_recipe_json(recipe),
                    recipe_schema_version=recipe.schema_version,
                    recipe_version=1,
                    recipe_hash=hash_recipe(recipe),
                    is_archived=0,
                )
                session.add(row)
                session.flush()
            rows[builtin_key] = row
        if config.default_signal_strategy_id is None:
            config.default_signal_strategy_id = rows["peak_value_with_volume"].id
        session.commit()


def _migration_strategy_name(n: int, x: float, y: float) -> str:
    return f"迁移 · {n}/{x:g}/{y:g}"


def _migrate_legacy_signal_strategy_assignments() -> None:
    with get_db() as session:
        session.execute(
            text(
                "CREATE TABLE IF NOT EXISTS schema_meta ("
                "key TEXT PRIMARY KEY NOT NULL, "
                "value TEXT NOT NULL)"
            )
        )
        if session.execute(
            text("SELECT value FROM schema_meta WHERE key = :key"),
            {"key": _SIGNAL_STRATEGIES_META_KEY},
        ).scalar():
            return

        config = session.get(StrategyConfig, 1)
        if config is None:
            return
        global_values = (
            max(int(config.global_buy_n), int(config.global_sell_n)),
            float(config.global_buy_x),
            float(config.global_sell_y),
        )
        grouped: dict[tuple[int, float, float], list[Watchlist]] = {}
        for watchlist in session.query(Watchlist).all():
            if watchlist.signal_strategy_id is not None:
                continue
            values = list(global_values)
            if watchlist.custom_n is not None:
                values[0] = int(watchlist.custom_n)
            if watchlist.custom_x is not None:
                values[1] = float(watchlist.custom_x)
            if watchlist.custom_y is not None:
                values[2] = float(watchlist.custom_y)
            override = session.get(StockStrategyOverride, watchlist.stock_code)
            if override is not None:
                if override.custom_n is not None:
                    values[0] = int(override.custom_n)
                if override.custom_x is not None:
                    values[1] = float(override.custom_x)
                if override.custom_y is not None:
                    values[2] = float(override.custom_y)
            effective = (int(values[0]), float(values[1]), float(values[2]))
            if effective != global_values:
                grouped.setdefault(effective, []).append(watchlist)

        for (n, x, y), watchlists in grouped.items():
            recipe = recipe_with_params(
                PEAK_VALUE_WITH_VOLUME_RECIPE,
                lookback_days=n,
                x=x,
                y=y,
                volume_lookback=7,
                buy_vol=0.0,
                sell_vol=0.30,
            )
            recipe_hash = hash_recipe(recipe)
            name = _migration_strategy_name(n, x, y)
            strategy = (
                session.query(SignalStrategy)
                .filter(
                    SignalStrategy.name == name,
                    SignalStrategy.recipe_hash == recipe_hash,
                )
                .one_or_none()
            )
            if strategy is None:
                candidate = name
                suffix = 2
                while (
                    session.query(SignalStrategy)
                    .filter(SignalStrategy.name == candidate)
                    .count()
                ):
                    candidate = f"{name} ({suffix})"
                    suffix += 1
                strategy = SignalStrategy(
                    name=candidate,
                    description="从旧版个股参数自动迁移",
                    recipe_json=_recipe_json(recipe),
                    recipe_schema_version=recipe.schema_version,
                    recipe_version=1,
                    recipe_hash=recipe_hash,
                    is_archived=0,
                )
                session.add(strategy)
                session.flush()
            for watchlist in watchlists:
                watchlist.signal_strategy_id = strategy.id

        session.execute(
            text("INSERT INTO schema_meta(key, value) VALUES (:key, :value)"),
            {"key": _SIGNAL_STRATEGIES_META_KEY, "value": "1"},
        )
        session.commit()


def _ensure_alert_log_columns() -> None:
    existing_columns = _existing_columns("alert_logs")
    if not existing_columns:
        return

    with get_engine().begin() as conn:
        if "error_code" not in existing_columns:
            conn.execute(text("ALTER TABLE alert_logs ADD COLUMN error_code VARCHAR(32)"))
        if "error_message" not in existing_columns:
            conn.execute(text("ALTER TABLE alert_logs ADD COLUMN error_message TEXT"))


def _ensure_stop_loss_pct_column() -> None:
    existing_columns = _existing_columns("strategy_config")
    if not existing_columns:
        return

    if "stop_loss_pct" in existing_columns:
        return

    with get_engine().begin() as conn:
        conn.execute(
            text("ALTER TABLE strategy_config ADD COLUMN stop_loss_pct REAL DEFAULT 0.08")
        )
        conn.execute(
            text(
                "UPDATE strategy_config SET stop_loss_pct = COALESCE(stop_loss_pct, 0.08)"
            )
        )


def _ensure_v2_risk_strategy_columns() -> None:
    existing_columns = _existing_columns("strategy_config")
    if not existing_columns:
        return

    alters: list[tuple[str, str]] = [
        ("break_even_trigger_pct", "REAL DEFAULT 0.10"),
        ("break_even_buffer_pct", "REAL DEFAULT 0.005"),
        ("trailing_ladder_json", "TEXT"),
        ("enable_partial_take_profit", "INTEGER DEFAULT 0"),
        ("enable_addon_alert", "INTEGER DEFAULT 0"),
        ("enable_tech_sell_while_holding", "INTEGER DEFAULT 0"),
    ]

    with get_engine().begin() as conn:
        for name, ddl in alters:
            if name not in existing_columns:
                conn.execute(text(f"ALTER TABLE strategy_config ADD COLUMN {name} {ddl}"))

        conn.execute(
            text(
                """
                UPDATE strategy_config
                SET break_even_trigger_pct = COALESCE(break_even_trigger_pct, 0.10),
                    break_even_buffer_pct = COALESCE(break_even_buffer_pct, 0.005),
                    enable_partial_take_profit = COALESCE(enable_partial_take_profit, 0),
                    enable_addon_alert = COALESCE(enable_addon_alert, 0),
                    enable_tech_sell_while_holding = COALESCE(enable_tech_sell_while_holding, 0)
                """
            )
        )
        conn.execute(
            text(
                """
                UPDATE strategy_config
                SET trailing_ladder_json = :ladder
                WHERE trailing_ladder_json IS NULL OR trailing_ladder_json = ''
                """
            ),
            {"ladder": DEFAULT_TRAILING_LADDER_JSON},
        )


def _ladder_signature(raw: str | list | None) -> list[tuple[float, float | None, float]]:
    levels = parse_trailing_ladder(raw)
    return [
        (
            round(level.min_pnl, 6),
            None if level.max_pnl is None else round(level.max_pnl, 6),
            round(level.drawdown, 6),
        )
        for level in levels
    ]


def _ladder_is_legacy_or_empty(raw: str | None) -> bool:
    if raw is None or not str(raw).strip():
        return True
    try:
        current = _ladder_signature(raw)
    except (TypeError, ValueError) as exc:
        logger.warning(
            "unparseable trailing_ladder_json; skipping factory risk migration: %s",
            exc,
        )
        return False
    return current == _ladder_signature(LEGACY_TRAILING_LADDER)


def _migrate_factory_risk_defaults() -> None:
    """Upgrade an untouched factory risk row to the growth-stock defaults.

    Skips the row if the user already changed stop, break-even, or trailing.
    """
    if not _existing_columns("strategy_config"):
        return
    with get_db() as session:
        row = session.get(StrategyConfig, 1)
        if row is None:
            return
        stop = float(row.stop_loss_pct) if row.stop_loss_pct is not None else None
        trigger = (
            float(row.break_even_trigger_pct)
            if row.break_even_trigger_pct is not None
            else None
        )
        if stop is None or trigger is None:
            return
        if abs(stop - LEGACY_STOP_LOSS_PCT) > 1e-12:
            return
        if abs(trigger - LEGACY_BREAK_EVEN_TRIGGER_PCT) > 1e-12:
            return
        if not _ladder_is_legacy_or_empty(row.trailing_ladder_json):
            return
        row.stop_loss_pct = DEFAULT_STOP_LOSS_PCT
        row.break_even_trigger_pct = DEFAULT_BREAK_EVEN_TRIGGER_PCT
        row.trailing_ladder_json = DEFAULT_TRAILING_LADDER_JSON
        session.commit()
        logger.info(
            "migrated factory risk defaults to stop=%.2f break-even=%.2f wide trailing",
            DEFAULT_STOP_LOSS_PCT,
            DEFAULT_BREAK_EVEN_TRIGGER_PCT,
        )


def get_price_adjust_version() -> str:
    """Current ``schema_meta.price_adjust`` value (defaults to qfq_v2 target)."""
    with get_engine().begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE IF NOT EXISTS schema_meta ("
                "key TEXT PRIMARY KEY NOT NULL, "
                "value TEXT NOT NULL)"
            )
        )
        return conn.execute(
            text("SELECT value FROM schema_meta WHERE key = :key"),
            {"key": _PRICE_ADJUST_META_KEY},
        ).scalar() or _PRICE_ADJUST_QFQ


def get_price_adjust_migrated_at() -> datetime | None:
    """When the snapshot cache was last cleared for a price-adjust migration."""
    with get_engine().begin() as conn:
        raw = conn.execute(
            text("SELECT value FROM schema_meta WHERE key = :key"),
            {"key": _PRICE_ADJUST_MIGRATED_AT_KEY},
        ).scalar()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw))
    except ValueError:
        logger.warning("invalid schema_meta.price_adjust_migrated_at=%r", raw)
        return None


def _migrate_snapshots_to_qfq() -> None:
    """Drop non-qfq OHLC cache once so the next job refetches forward-adjusted bars."""
    with get_engine().begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE IF NOT EXISTS schema_meta ("
                "key TEXT PRIMARY KEY NOT NULL, "
                "value TEXT NOT NULL)"
            )
        )
        current = conn.execute(
            text("SELECT value FROM schema_meta WHERE key = :key"),
            {"key": _PRICE_ADJUST_META_KEY},
        ).scalar()
        if current == _PRICE_ADJUST_QFQ:
            return
        tables = {
            name
            for (name,) in conn.execute(
                text("SELECT name FROM sqlite_master WHERE type = 'table'")
            )
        }
        if "daily_market_snapshots" in tables:
            conn.execute(text("DELETE FROM daily_market_snapshots"))
        if "daily_baselines" in tables:
            conn.execute(text("DELETE FROM daily_baselines"))
        migrated_at = datetime.now().replace(microsecond=0).isoformat()
        conn.execute(
            text(
                "INSERT INTO schema_meta(key, value) VALUES (:key, :value) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value"
            ),
            {"key": _PRICE_ADJUST_META_KEY, "value": _PRICE_ADJUST_QFQ},
        )
        conn.execute(
            text(
                "INSERT INTO schema_meta(key, value) VALUES (:key, :value) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value"
            ),
            {"key": _PRICE_ADJUST_MIGRATED_AT_KEY, "value": migrated_at},
        )
        logger.info(
            "cleared %s snapshot/baseline cache; next fetch uses %s bars",
            current or "missing",
            _PRICE_ADJUST_QFQ,
        )


def init_db() -> None:
    enable_wal()
    Base.metadata.create_all(get_engine())
    _ensure_strategy_columns()
    _ensure_signal_strategy_columns()
    _ensure_alert_log_columns()
    _ensure_stop_loss_pct_column()
    _ensure_v2_risk_strategy_columns()

    with get_db() as session:
        stmt = (
            sqlite_insert(StrategyConfig)
            .values(
                id=1,
                global_buy_n=60,
                global_buy_x=1.10,
                global_sell_n=60,
                global_sell_y=0.90,
                stop_loss_pct=DEFAULT_STOP_LOSS_PCT,
                break_even_trigger_pct=DEFAULT_BREAK_EVEN_TRIGGER_PCT,
                break_even_buffer_pct=DEFAULT_BREAK_EVEN_BUFFER_PCT,
                trailing_ladder_json=DEFAULT_TRAILING_LADDER_JSON,
                enable_partial_take_profit=0,
                enable_addon_alert=0,
                enable_tech_sell_while_holding=0,
            )
            .on_conflict_do_nothing(index_elements=["id"])
        )
        session.execute(stmt)
        session.commit()

    _seed_builtin_signal_strategies()
    _migrate_legacy_signal_strategy_assignments()
    _migrate_factory_risk_defaults()
    _migrate_snapshots_to_qfq()
