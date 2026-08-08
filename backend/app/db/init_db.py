from __future__ import annotations

from sqlalchemy import inspect, text
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.db.connection import enable_wal, get_db, get_engine
from app.db.models import Base, StrategyConfig
from app.schemas.strategy import DEFAULT_TRAILING_LADDER_JSON


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


def init_db() -> None:
    enable_wal()
    Base.metadata.create_all(get_engine())
    _ensure_strategy_columns()
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
                stop_loss_pct=0.08,
                break_even_trigger_pct=0.10,
                break_even_buffer_pct=0.005,
                trailing_ladder_json=DEFAULT_TRAILING_LADDER_JSON,
                enable_partial_take_profit=0,
                enable_addon_alert=0,
                enable_tech_sell_while_holding=0,
            )
            .on_conflict_do_nothing(index_elements=["id"])
        )
        session.execute(stmt)
        session.commit()
