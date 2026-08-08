from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator


DEFAULT_TRAILING_LADDER: list[dict[str, float | None]] = [
    {"min_pnl": 0.10, "max_pnl": 0.20, "drawdown": 0.15},
    {"min_pnl": 0.20, "max_pnl": 0.50, "drawdown": 0.10},
    {"min_pnl": 0.50, "max_pnl": None, "drawdown": 0.06},
]

DEFAULT_TRAILING_LADDER_JSON = json.dumps(DEFAULT_TRAILING_LADDER, separators=(",", ":"))

# Suggested sell ratios when crossing into each ladder band (PRD leaves X undefined).
PARTIAL_TP_SUGGEST_RATIOS: tuple[float, ...] = (0.30, 0.30, 0.40)


class TrailingLadderLevel(BaseModel):
    min_pnl: float = Field(ge=0, lt=1)
    max_pnl: float | None = Field(default=None)
    drawdown: float = Field(gt=0, lt=1)

    @field_validator("max_pnl")
    @classmethod
    def _max_pnl_range(cls, value: float | None) -> float | None:
        if value is None:
            return None
        if value <= 0 or value > 1:
            raise ValueError("max_pnl must be in (0, 1] or null")
        return value

    @model_validator(mode="after")
    def _min_lt_max(self) -> TrailingLadderLevel:
        if self.max_pnl is not None and self.min_pnl >= self.max_pnl:
            raise ValueError("min_pnl must be < max_pnl")
        return self


def validate_trailing_ladder(levels: list[TrailingLadderLevel]) -> list[TrailingLadderLevel]:
    if not levels:
        raise ValueError("trailing_ladder must not be empty")
    sorted_levels = sorted(levels, key=lambda item: item.min_pnl)
    for i, level in enumerate(sorted_levels):
        if i == 0:
            continue
        prev = sorted_levels[i - 1]
        if prev.max_pnl is None:
            raise ValueError("only the last ladder level may have null max_pnl")
        if level.min_pnl < prev.max_pnl - 1e-12:
            raise ValueError("trailing ladder bands must not overlap")
    if sorted_levels[-1].max_pnl is not None:
        # Allow closed last band; prefer open-ended for >=50% style defaults.
        pass
    return sorted_levels


def parse_trailing_ladder(raw: str | list | None) -> list[TrailingLadderLevel]:
    if raw is None:
        data: Any = DEFAULT_TRAILING_LADDER
    elif isinstance(raw, list):
        data = raw
    else:
        data = json.loads(raw)
    levels = [TrailingLadderLevel.model_validate(item) for item in data]
    return validate_trailing_ladder(levels)


def ladder_to_json(levels: list[TrailingLadderLevel]) -> str:
    return json.dumps(
        [level.model_dump() for level in levels],
        separators=(",", ":"),
    )


class StrategyConfig(BaseModel):
    global_buy_n: int = Field(gt=0)
    global_buy_x: float = Field(gt=0)
    global_sell_n: int = Field(gt=0)
    global_sell_y: float = Field(gt=0)
    stop_loss_pct: float = Field(default=0.08, ge=0, lt=1)
    break_even_trigger_pct: float = Field(default=0.10, ge=0, lt=1)
    break_even_buffer_pct: float = Field(default=0.005, ge=0, lt=1)
    trailing_ladder: list[TrailingLadderLevel] = Field(
        default_factory=lambda: parse_trailing_ladder(DEFAULT_TRAILING_LADDER)
    )
    enable_partial_take_profit: bool = False
    enable_addon_alert: bool = False
    enable_tech_sell_while_holding: bool = False

    @field_validator("trailing_ladder")
    @classmethod
    def _validate_ladder(
        cls, value: list[TrailingLadderLevel]
    ) -> list[TrailingLadderLevel]:
        return validate_trailing_ladder(value)


class StockStrategyOverride(BaseModel):
    """Nullable fields follow global; omit or null = no override for that field."""

    custom_n: int | None = Field(default=None, gt=0)
    custom_x: float | None = Field(default=None, gt=0)
    custom_y: float | None = Field(default=None, gt=0)
    stop_loss_pct: float | None = Field(default=None, ge=0, lt=1)
    break_even_trigger_pct: float | None = Field(default=None, ge=0, lt=1)
    break_even_buffer_pct: float | None = Field(default=None, ge=0, lt=1)
    trailing_ladder: list[TrailingLadderLevel] | None = None
    enable_partial_take_profit: bool | None = None
    enable_addon_alert: bool | None = None
    enable_tech_sell_while_holding: bool | None = None

    @field_validator("trailing_ladder")
    @classmethod
    def _validate_ladder(
        cls, value: list[TrailingLadderLevel] | None
    ) -> list[TrailingLadderLevel] | None:
        if value is None:
            return None
        return validate_trailing_ladder(value)


class ResolvedStrategyParams(BaseModel):
    n: int
    x: float
    y: float
    stop_loss_pct: float
    break_even_trigger_pct: float
    break_even_buffer_pct: float
    trailing_ladder: list[TrailingLadderLevel]
    enable_partial_take_profit: bool
    enable_addon_alert: bool
    enable_tech_sell_while_holding: bool
