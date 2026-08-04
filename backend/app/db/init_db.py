from pathlib import Path

from app.db.connection import get_db


def _ensure_strategy_columns(conn) -> None:
    existing_columns = {
        row["name"] for row in conn.execute("PRAGMA table_info(strategy_config)").fetchall()
    }

    if "global_buy_n" not in existing_columns:
        conn.execute("ALTER TABLE strategy_config ADD COLUMN global_buy_n INTEGER DEFAULT 60")
    if "global_buy_x" not in existing_columns:
        conn.execute("ALTER TABLE strategy_config ADD COLUMN global_buy_x REAL DEFAULT 1.10")
    if "global_sell_n" not in existing_columns:
        conn.execute("ALTER TABLE strategy_config ADD COLUMN global_sell_n INTEGER DEFAULT 60")
    if "global_sell_y" not in existing_columns:
        conn.execute("ALTER TABLE strategy_config ADD COLUMN global_sell_y REAL DEFAULT 0.90")

    legacy_n_expr = "global_n" if "global_n" in existing_columns else "NULL"
    legacy_x_expr = "global_x" if "global_x" in existing_columns else "NULL"
    legacy_y_expr = "global_y" if "global_y" in existing_columns else "NULL"

    # Copy legacy global_n/global_x/global_y into the new buy/sell fields when available.
    conn.execute(
        f"""
        UPDATE strategy_config
        SET global_buy_n = COALESCE(global_buy_n, {legacy_n_expr}, 60),
            global_buy_x = COALESCE(global_buy_x, {legacy_x_expr}, 1.10),
            global_sell_n = COALESCE(global_sell_n, {legacy_n_expr}, 60),
            global_sell_y = COALESCE(global_sell_y, {legacy_y_expr}, 0.90)
        """
    )


def init_db() -> None:
    schema_path = Path(__file__).with_name("schema.sql")
    sql = schema_path.read_text(encoding="utf-8")

    with get_db() as conn:
        conn.executescript(sql)
        _ensure_strategy_columns(conn)
        conn.commit()

