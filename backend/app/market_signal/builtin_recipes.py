"""Builtin recipes matching the current strategy defaults."""

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

__all__ = [
    "BUILTIN_RECIPES",
    "PEAK_VALLEY_RECIPE",
    "PEAK_VALUE_WITH_VOLUME_RECIPE",
    "peak_valley",
    "peak_value_with_volume",
]
