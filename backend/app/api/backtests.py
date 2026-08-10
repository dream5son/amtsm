from fastapi import APIRouter, HTTPException, Query

from app.schemas.backtest import (
    BacktestApplyRequest,
    BacktestApplyResponse,
    BacktestCreateRequest,
    BacktestCreateResponse,
    BacktestJobResponse,
    BacktestKlineResponse,
)
from app.services.backtest_service import (
    BacktestApplyError,
    BacktestJobNotFoundError,
    StockNotFoundError,
    apply_job_params,
    create_jobs,
    get_job,
    get_kline_data,
    list_jobs_by_compare_group,
    list_jobs_by_stock,
)

router = APIRouter(prefix="/api/backtests", tags=["backtests"])


@router.post("", status_code=201)
def post_backtests(payload: BacktestCreateRequest) -> BacktestCreateResponse:
    try:
        result = create_jobs(
            payload.stock_code,
            payload.start_date,
            payload.end_date,
            [item.model_dump(exclude_none=True) for item in payload.params],
        )
    except StockNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return BacktestCreateResponse(**result)


@router.get("")
def get_backtests(
    stock: str | None = Query(default=None),
    compare_group: str | None = Query(default=None),
) -> list[BacktestJobResponse]:
    if stock and compare_group:
        raise HTTPException(
            status_code=400, detail="provide either stock or compare_group, not both"
        )
    if compare_group:
        return list_jobs_by_compare_group(compare_group)
    if stock:
        return list_jobs_by_stock(stock)
    raise HTTPException(status_code=400, detail="stock or compare_group is required")


@router.get("/{job_id}")
def get_backtest(job_id: int) -> BacktestJobResponse:
    try:
        return get_job(job_id)
    except BacktestJobNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/{job_id}/kline")
def get_backtest_kline(job_id: int) -> BacktestKlineResponse:
    try:
        return get_kline_data(job_id)
    except BacktestJobNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/{job_id}/apply")
def post_backtest_apply(
    job_id: int, payload: BacktestApplyRequest | None = None
) -> BacktestApplyResponse:
    body = payload or BacktestApplyRequest()
    try:
        result = apply_job_params(job_id, update_global=body.update_global)
    except BacktestJobNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except StockNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except BacktestApplyError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return BacktestApplyResponse(**result)
