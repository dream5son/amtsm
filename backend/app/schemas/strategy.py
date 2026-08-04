from pydantic import BaseModel, Field


class StrategyConfig(BaseModel):
    global_buy_n: int = Field(gt=0)
    global_buy_x: float = Field(gt=0)
    global_sell_n: int = Field(gt=0)
    global_sell_y: float = Field(gt=0)
