from fastapi import APIRouter, HTTPException

from app.schemas.watchlist import WatchlistCreate
from app.services.watchlist_service import (
    add_watchlist,
    list_watchlist,
    remove_watchlist,
)

router = APIRouter(prefix="/api/watchlist", tags=["watchlist"])


@router.get("")
def get_watchlist(limit: int = 50, offset: int = 0) -> list[dict]:
    try:
        return list_watchlist(limit=limit, offset=offset)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("", status_code=201)
def create_watchlist(payload: WatchlistCreate) -> dict:
    try:
        add_watchlist(payload)
    except ValueError as exc:
        detail = str(exc)
        if detail == "stock already exists":
            raise HTTPException(status_code=409, detail=detail) from exc
        raise HTTPException(status_code=400, detail=detail) from exc

    return {"message": "created"}


@router.delete("/{stock_code}")
def delete_watchlist(stock_code: str) -> dict:
    try:
        rows = remove_watchlist(stock_code)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if rows == 0:
        raise HTTPException(status_code=404, detail="stock not found")
    return {"message": "removed"}
