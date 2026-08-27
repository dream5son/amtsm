"""Schema and canonicalization for V1 signal recipes."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    StrictStr,
    field_validator,
)

from app.market_signal.operators.registry import DEFAULT_OPERATOR_REGISTRY


class OperatorCall(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    op: StrictStr = Field(min_length=1)
    version: StrictInt
    params: dict[str, Any]


class RecipeChannelV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    all: tuple[OperatorCall, ...] = Field(min_length=1)


class RecipeChannelsV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    buy: RecipeChannelV1
    sell: RecipeChannelV1
    addon: RecipeChannelV1 | None = None


class StrategyRecipeV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: StrictInt
    channels: RecipeChannelsV1

    @field_validator("schema_version")
    @classmethod
    def _v1_only(cls, value: int) -> int:
        if value != 1:
            raise ValueError("schema_version must be 1")
        return value


ChannelRecipeV1 = RecipeChannelV1
type RecipeInput = StrategyRecipeV1 | Mapping[str, Any] | str | bytes


def _parse_recipe(recipe: RecipeInput) -> StrategyRecipeV1:
    if isinstance(recipe, (str, bytes)):
        return StrategyRecipeV1.model_validate_json(recipe)
    return StrategyRecipeV1.model_validate(recipe)


def _normalize_calls(
    channel: str, calls: tuple[OperatorCall, ...]
) -> tuple[OperatorCall, ...]:
    normalized: list[OperatorCall] = []
    for call in calls:
        key = f"{call.op}@{call.version}"
        spec = DEFAULT_OPERATOR_REGISTRY.get(key)
        if spec is None:
            raise ValueError(f"unknown operator: {key}")
        if channel not in spec.channels:
            raise ValueError(f"operator {key} is not allowed in {channel}")
        params = spec.validate_params(call.params)
        normalized.append(
            call.model_copy(update={"params": params.model_dump(mode="json")})
        )
    return tuple(normalized)


def _require_leading_price(
    channel: str,
    calls: tuple[OperatorCall, ...],
    expected_op: str,
) -> None:
    expected_key = f"{expected_op}@1"
    first_key = f"{calls[0].op}@{calls[0].version}"
    count = sum(call.op == expected_op and call.version == 1 for call in calls)
    if first_key != expected_key or count != 1:
        raise ValueError(f"{channel} must start with exactly one {expected_key}")


def _validate_recipe_shape(recipe: StrategyRecipeV1) -> None:
    channels = recipe.channels
    _require_leading_price("buy", channels.buy.all, "price_near_low")
    _require_leading_price("sell", channels.sell.all, "price_near_high")

    if channels.addon is not None:
        _require_leading_price("addon", channels.addon.all, "price_near_low")
        if channels.addon.all[0].params != channels.buy.all[0].params:
            raise ValueError("addon price params must match buy price params")

    calls = [*channels.buy.all, *channels.sell.all]
    if channels.addon is not None:
        calls.extend(channels.addon.all)

    price_lookbacks = {
        call.params["lookback_days"]
        for call in calls
        if call.op in {"price_near_low", "price_near_high"}
    }
    if len(price_lookbacks) != 1:
        raise ValueError("all price gates must share one lookback_days")

    volume_lookbacks = {
        call.params["lookback_days"]
        for call in calls
        if call.op in {"volume_increase", "volume_abs_change"}
    }
    if len(volume_lookbacks) > 1:
        raise ValueError("all volume gates must share one lookback_days")


def validate_recipe(recipe: RecipeInput) -> StrategyRecipeV1:
    parsed = _parse_recipe(recipe)
    addon = parsed.channels.addon
    channels = RecipeChannelsV1(
        buy=RecipeChannelV1(all=_normalize_calls("buy", parsed.channels.buy.all)),
        sell=RecipeChannelV1(all=_normalize_calls("sell", parsed.channels.sell.all)),
        addon=(
            RecipeChannelV1(all=_normalize_calls("addon", addon.all))
            if addon is not None
            else None
        ),
    )
    validated = parsed.model_copy(update={"channels": channels})
    _validate_recipe_shape(validated)
    return validated


def normalize(recipe: RecipeInput) -> dict[str, Any]:
    return validate_recipe(recipe).model_dump(mode="json")


normalize_recipe = normalize


def hash_recipe(recipe: RecipeInput) -> str:
    payload = json.dumps(
        normalize(recipe),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


__all__ = [
    "ChannelRecipeV1",
    "OperatorCall",
    "RecipeChannelV1",
    "RecipeChannelsV1",
    "StrategyRecipeV1",
    "hash_recipe",
    "normalize",
    "normalize_recipe",
    "validate_recipe",
]
