from __future__ import annotations

from datetime import datetime

from sqlalchemy import select

from app.db.connection import get_db
from app.db.models import StrategyConfig
from app.schemas.strategy import StrategyConfig as StrategyConfigSchema


def get_strategy() -> dict:
    with get_db() as session:
        row = session.scalars(
            select(StrategyConfig).where(StrategyConfig.id == 1)
        ).first()
    if row is None:
        return {
            "global_buy_n": 60,
            "global_buy_x": 1.1,
            "global_sell_n": 60,
            "global_sell_y": 0.9,
        }
    return {
        "global_buy_n": row.global_buy_n,
        "global_buy_x": row.global_buy_x,
        "global_sell_n": row.global_sell_n,
        "global_sell_y": row.global_sell_y,
    }


def update_strategy(payload: StrategyConfigSchema) -> dict:
    with get_db() as session:
        row = session.scalars(
            select(StrategyConfig).where(StrategyConfig.id == 1)
        ).first()
        if row is None:
            row = StrategyConfig(id=1)
            session.add(row)
        row.global_buy_n = payload.global_buy_n
        row.global_buy_x = payload.global_buy_x
        row.global_sell_n = payload.global_sell_n
        row.global_sell_y = payload.global_sell_y
        row.updated_at = datetime.now()
        session.commit()
    return get_strategy()
