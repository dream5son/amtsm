"""Builtin recipes matching the current strategy defaults."""

from copy import deepcopy

from app.market_signal.recipe_schema import StrategyRecipeV1, validate_recipe

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
    recipe = deepcopy(base_recipe.model_dump(mode="json"))
    for channel in recipe["channels"].values():
        if channel is None:
            continue
        for call in channel["all"]:
            if call["op"] == "price_near_low":
                call["params"] = {
                    "lookback_days": int(lookback_days),
                    "factor": float(x),
                }
            elif call["op"] == "price_near_high":
                call["params"] = {
                    "lookback_days": int(lookback_days),
                    "factor": float(y),
                }
            elif call["op"] == "volume_increase" and volume_lookback is not None:
                call["params"] = {
                    "lookback_days": int(volume_lookback),
                    "min_change_pct": float(buy_vol or 0.0),
                }
            elif call["op"] == "volume_abs_change" and volume_lookback is not None:
                call["params"] = {
                    "lookback_days": int(volume_lookback),
                    "min_abs_change_pct": float(sell_vol or 0.0),
                }
    return validate_recipe(recipe)


__all__ = [
    "BUILTIN_RECIPES",
    "PEAK_VALLEY_RECIPE",
    "PEAK_VALUE_WITH_VOLUME_RECIPE",
    "peak_valley",
    "peak_value_with_volume",
    "recipe_with_params",
]
