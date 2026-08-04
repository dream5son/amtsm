from pydantic import BaseModel, Field


class StockSearchItem(BaseModel):
    stock_code: str = Field(description="normalized code, e.g. sh600519")
    stock_name: str
    exchange: str = Field(description="SH/SZ/BJ")
