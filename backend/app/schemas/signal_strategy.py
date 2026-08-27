from __future__ import annotations

from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


class SignalStrategyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: str | None = None
    recipe: dict[str, Any]


class SignalStrategyUpdate(BaseModel):
    expected_version: int = Field(gt=0)
    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = None
    recipe: dict[str, Any] | None = None


class SignalStrategyClone(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class SignalStrategyDryRun(BaseModel):
    stock_code: str = Field(min_length=6, max_length=10)
    recipe: dict[str, Any] | None = None
    strategy_id: int | None = Field(default=None, gt=0)
    as_of: date | None = None
    policy: Literal["live"] = "live"

    @model_validator(mode="after")
    def _one_recipe_source(self) -> SignalStrategyDryRun:
        if (self.recipe is None) == (self.strategy_id is None):
            raise ValueError("provide exactly one of recipe or strategy_id")
        return self


class SignalStrategyDefaultUpdate(BaseModel):
    strategy_id: int = Field(gt=0)


class WatchlistSignalStrategyUpdate(BaseModel):
    strategy_id: int | None = Field(default=None, gt=0)
