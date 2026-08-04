from app.db.connection import get_db
from app.schemas.strategy import StrategyConfig


def get_strategy() -> dict:
    with get_db() as conn:
        row = conn.execute(
            """
            SELECT global_buy_n, global_buy_x, global_sell_n, global_sell_y
            FROM strategy_config
            WHERE id = 1
            """
        ).fetchone()
    return (
        dict(row)
        if row
        else {
            "global_buy_n": 60,
            "global_buy_x": 1.1,
            "global_sell_n": 60,
            "global_sell_y": 0.9,
        }
    )


def update_strategy(payload: StrategyConfig) -> dict:
    with get_db() as conn:
        conn.execute(
            """
            UPDATE strategy_config
            SET global_buy_n = ?,
                global_buy_x = ?,
                global_sell_n = ?,
                global_sell_y = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = 1
            """,
            (
                payload.global_buy_n,
                payload.global_buy_x,
                payload.global_sell_n,
                payload.global_sell_y,
            ),
        )
        conn.commit()
    return get_strategy()
