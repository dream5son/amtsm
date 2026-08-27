"""Builtin recipes matching the current strategy defaults."""

from copy import deepcopy

from app.market_signal.recipe_schema import (
    RecipeInput,
    StrategyRecipeV1,
    validate_recipe,
)

PEAK_VALLEY_RECIPE: StrategyRecipeV1 = validate_recipe(
    {
        "schema_version": 1,
        "channels": {
            "buy": {
                "all": [
                    {
                        "op": "price_near_low",
                        "version": 1,
                        "params": {"lookback_days": 60, "factor": 1.1},
                    }
                ]
            },
            "sell": {
                "all": [
                    {
                        "op": "price_near_high",
                        "version": 1,
                        "params": {"lookback_days": 60, "factor": 0.9},
                    }
                ]
            },
            "addon": {
                "all": [
                    {
                        "op": "price_near_low",
                        "version": 1,
                        "params": {"lookback_days": 60, "factor": 1.1},
                    }
                ]
            },
        },
    }
)

PEAK_VALUE_WITH_VOLUME_RECIPE: StrategyRecipeV1 = validate_recipe(
    {
        "schema_version": 1,
        "channels": {
            "buy": {
                "all": [
                    {
                        "op": "price_near_low",
                        "version": 1,
                        "params": {"lookback_days": 60, "factor": 1.1},
                    },
                    {
                        "op": "volume_increase",
                        "version": 1,
                        "params": {
                            "lookback_days": 7,
                            "min_change_pct": 0.0,
                        },
                    },
                ]
            },
            "sell": {
                "all": [
                    {
                        "op": "price_near_high",
                        "version": 1,
                        "params": {"lookback_days": 60, "factor": 0.9},
                    },
                    {
                        "op": "volume_abs_change",
                        "version": 1,
                        "params": {
                            "lookback_days": 7,
                            "min_abs_change_pct": 0.30,
                        },
                    },
                ]
            },
            "addon": {
                "all": [
                    {
                        "op": "price_near_low",
                        "version": 1,
                        "params": {"lookback_days": 60, "factor": 1.1},
                    },
                    {
                        "op": "volume_increase",
                        "version": 1,
                        "params": {
                            "lookback_days": 7,
                            "min_change_pct": 0.0,
                        },
                    },
                ]
            },
        },
    }
)

peak_valley = PEAK_VALLEY_RECIPE
peak_value_with_volume = PEAK_VALUE_WITH_VOLUME_RECIPE

BUILTIN_RECIPES = {
    "peak_valley": peak_valley,
    "peak_value_with_volume": peak_value_with_volume,
}


def extract_price_params(recipe: RecipeInput) -> tuple[int, float, float]:
    validated = validate_recipe(recipe)
    buy = validated.channels.buy.all[0].params
    sell = validated.channels.sell.all[0].params
    return (
        int(buy["lookback_days"]),
        float(buy["factor"]),
        float(sell["factor"]),
    )


def rewrite_recipe_params(
    recipe: RecipeInput,
    *,
    lookback_days: int | None = None,
    x: float | None = None,
    y: float | None = None,
    volume_lookback: int | None = None,
    buy_vol: float | None = None,
    sell_vol: float | None = None,
) -> StrategyRecipeV1:
    recipe = deepcopy(validate_recipe(recipe).model_dump(mode="json"))
    for channel in recipe["channels"].values():
        if channel is None:
            continue
        for call in channel["all"]:
            if call["op"] == "price_near_low":
                call["params"] = {
                    "lookback_days": int(
                        lookback_days
                        if lookback_days is not None
                        else call["params"]["lookback_days"]
                    ),
                    "factor": float(x if x is not None else call["params"]["factor"]),
                }
            elif call["op"] == "price_near_high":
                call["params"] = {
                    "lookback_days": int(
                        lookback_days
                        if lookback_days is not None
                        else call["params"]["lookback_days"]
                    ),
                    "factor": float(y if y is not None else call["params"]["factor"]),
                }
            elif call["op"] == "volume_increase" and (
                volume_lookback is not None or buy_vol is not None
            ):
                call["params"] = {
                    "lookback_days": int(
                        volume_lookback
                        if volume_lookback is not None
                        else call["params"]["lookback_days"]
                    ),
                    "min_change_pct": float(
                        buy_vol
                        if buy_vol is not None
                        else call["params"]["min_change_pct"]
                    ),
                }
            elif call["op"] == "volume_abs_change" and (
                volume_lookback is not None or sell_vol is not None
            ):
                call["params"] = {
                    "lookback_days": int(
                        volume_lookback
                        if volume_lookback is not None
                        else call["params"]["lookback_days"]
                    ),
                    "min_abs_change_pct": float(
                        sell_vol
                        if sell_vol is not None
                        else call["params"]["min_abs_change_pct"]
                    ),
                }
    return validate_recipe(recipe)


def recipe_with_params(
    base_recipe: StrategyRecipeV1,
    *,
    lookback_days: int,
    x: float,
    y: float,
    volume_lookback: int | None = None,
    buy_vol: float | None = None,
    sell_vol: float | None = None,
) -> StrategyRecipeV1:
    return rewrite_recipe_params(
        base_recipe,
        lookback_days=lookback_days,
        x=x,
        y=y,
        volume_lookback=volume_lookback,
        buy_vol=buy_vol,
        sell_vol=sell_vol,
    )


__all__ = [
    "BUILTIN_RECIPES",
    "PEAK_VALLEY_RECIPE",
    "PEAK_VALUE_WITH_VOLUME_RECIPE",
    "extract_price_params",
    "peak_valley",
    "peak_value_with_volume",
    "recipe_with_params",
    "rewrite_recipe_params",
]
