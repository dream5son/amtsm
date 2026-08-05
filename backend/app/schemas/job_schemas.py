from pydantic import BaseModel


class JobStatusResponse(BaseModel):
    status: str  # 未开始 | 执行中 | 执行失败 | 执行成功
    latest_job: dict | None = None


class JobLogItemResponse(BaseModel):
    id: int
    stock_code: str
    stock_name: str | None
    strategy_n: int
    actual_n: int | None
    status: str
    low_min: float | None
    high_max: float | None
    error_message: str | None
    processed_at: str | None


class JobLogResponse(BaseModel):
    job_log: dict
    items: list[dict]
