from __future__ import annotations

from copy import deepcopy

import pytest
from pydantic import ValidationError

from app.market_signal.builtin_recipes import (
    PEAK_VALLEY_RECIPE,
    PEAK_VALUE_WITH_VOLUME_RECIPE,
)
from app.market_signal.compiler import (
    EvaluationPolicy,
    FeedRequirements,
    analyze_recipe,
    compile_recipe,
    dry_run_recipe,
)
from app.market_signal.feed import BaselineWindow, StaticBaselineFeed, VolumeWindow
from app.market_signal.peak_valley import PeakValleyStrategy
from app.market_signal.peak_value_with_volume import PeakValueWithVolumeStrategy
from app.market_signal.recipe_schema import (
    StrategyRecipeV1,
    hash_recipe,
    normalize,
    validate_recipe,
)
from app.market_signal.strategy import SignalRequest, StrategyParams


def _request(
    price: float,
    *,
    volume: float | None = None,
    low_min: float = 100.0,
    high_max: float = 130.0,
    actual_n: int | None = 60,
    requested_n: int | None = 60,
    volume_n: int = 7,
    avg_volume: float = 1000.0,
) -> SignalRequest:
    return SignalRequest(
        price=price,
        volume=volume,
        feed=StaticBaselineFeed(
            BaselineWindow(
                low_min=low_min,
                high_max=high_max,
                actual_n=actual_n,
                requested_n=requested_n,
            ),
            VolumeWindow(
                avg_volume=avg_volume,
                actual_n=volume_n,
                lookback=7,
            ),
        ),
        params=StrategyParams(
            x=1.1,
            y=0.9,
            volume_lookback=7,
            buy_volume_increase_pct=0.0,
            sell_volume_change_pct=0.30,
        ),
    )


def _volume_increase_recipe(threshold: float = 0.30) -> dict:
    recipe = normalize(PEAK_VALLEY_RECIPE)
    gate = {
        "op": "volume_increase",
        "version": 1,
        "params": {
            "lookback_days": 7,
            "min_change_pct": threshold,
        },
    }
    recipe["channels"]["buy"]["all"].append(deepcopy(gate))
    recipe["channels"]["addon"]["all"].append(deepcopy(gate))
    return recipe


def test_validate_normalize_and_hash_recipe() -> None:
    raw = normalize(PEAK_VALLEY_RECIPE)
    parsed = validate_recipe(raw)
    reordered = {
        "channels": raw["channels"],
        "schema_version": raw["schema_version"],
    }

    assert isinstance(parsed, StrategyRecipeV1)
    assert normalize(parsed) == raw
    assert hash_recipe(reordered) == hash_recipe(parsed)
    assert len(hash_recipe(parsed)) == 64


def test_analyze_recipe_reports_active_feed_requirements() -> None:
    assert analyze_recipe(PEAK_VALLEY_RECIPE) == FeedRequirements(
        price_lookback_days=60
    )
    assert analyze_recipe(PEAK_VALUE_WITH_VOLUME_RECIPE) == FeedRequirements(
        price_lookback_days=60,
        volume_lookback_days=7,
        requires_current_volume=True,
    )
    assert analyze_recipe(_volume_increase_recipe(0.0)) == FeedRequirements(
        price_lookback_days=60
    )


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("extra",), True),
        (("channels", "extra"), True),
        (("channels", "buy", "extra"), True),
        (("channels", "buy", "all", 0, "extra"), True),
        (("channels", "buy", "all", 0, "params", "extra"), True),
    ],
)
def test_unknown_fields_are_rejected(path: tuple, value: object) -> None:
    recipe = normalize(PEAK_VALLEY_RECIPE)
    target = recipe
    for part in path[:-1]:
        target = target[part]
    target[path[-1]] = value

    with pytest.raises((ValidationError, ValueError), match="extra"):
        validate_recipe(recipe)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("op", "python_eval", "unknown operator"),
        ("version", 2, "unknown operator"),
    ],
)
def test_unknown_operator_or_version_is_rejected(
    field: str, value: object, message: str
) -> None:
    recipe = normalize(PEAK_VALLEY_RECIPE)
    recipe["channels"]["buy"]["all"][0][field] = value

    with pytest.raises(ValueError, match=message):
        validate_recipe(recipe)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("lookback_days", 0),
        ("lookback_days", -1),
        ("factor", 0.0),
        ("factor", -1.0),
    ],
)
def test_invalid_price_params_are_rejected(field: str, value: object) -> None:
    recipe = normalize(PEAK_VALLEY_RECIPE)
    recipe["channels"]["buy"]["all"][0]["params"][field] = value

    with pytest.raises(ValidationError):
        validate_recipe(recipe)


@pytest.mark.parametrize("threshold", [-0.01, float("inf"), float("nan")])
def test_invalid_volume_threshold_is_rejected(threshold: float) -> None:
    recipe = _volume_increase_recipe()
    recipe["channels"]["buy"]["all"][1]["params"]["min_change_pct"] = threshold

    with pytest.raises(ValidationError):
        validate_recipe(recipe)


def test_empty_channel_is_rejected() -> None:
    recipe = normalize(PEAK_VALLEY_RECIPE)
    recipe["channels"]["buy"]["all"] = []

    with pytest.raises(ValidationError):
        validate_recipe(recipe)


def test_price_gate_order_count_and_channel_are_enforced() -> None:
    not_first = _volume_increase_recipe()
    not_first["channels"]["buy"]["all"].reverse()
    with pytest.raises(ValueError, match="must start"):
        validate_recipe(not_first)

    duplicate = normalize(PEAK_VALLEY_RECIPE)
    duplicate["channels"]["buy"]["all"].append(
        deepcopy(duplicate["channels"]["buy"]["all"][0])
    )
    with pytest.raises(ValueError, match="exactly one"):
        validate_recipe(duplicate)

    wrong_channel = normalize(PEAK_VALLEY_RECIPE)
    wrong_channel["channels"]["buy"]["all"][0]["op"] = "price_near_high"
    with pytest.raises(ValueError, match="not allowed"):
        validate_recipe(wrong_channel)


def test_lookbacks_and_addon_price_params_are_consistent() -> None:
    price_mismatch = normalize(PEAK_VALLEY_RECIPE)
    price_mismatch["channels"]["sell"]["all"][0]["params"]["lookback_days"] = 30
    with pytest.raises(ValueError, match="price gates"):
        validate_recipe(price_mismatch)

    addon_mismatch = normalize(PEAK_VALLEY_RECIPE)
    addon_mismatch["channels"]["addon"]["all"][0]["params"]["factor"] = 1.2
    with pytest.raises(ValueError, match="addon price params"):
        validate_recipe(addon_mismatch)

    volume_mismatch = _volume_increase_recipe()
    volume_mismatch["channels"]["sell"]["all"].append(
        {
            "op": "volume_abs_change",
            "version": 1,
            "params": {
                "lookback_days": 5,
                "min_abs_change_pct": 0.3,
            },
        }
    )
    with pytest.raises(ValueError, match="volume gates"):
        validate_recipe(volume_mismatch)


def test_addon_can_be_disabled() -> None:
    recipe = normalize(PEAK_VALLEY_RECIPE)
    recipe["channels"]["addon"] = None

    result = dry_run_recipe(recipe, _request(110.0))

    assert result.decision.buy
    assert not result.decision.addon
    assert not result.channels["addon"].present
    assert result.channels["addon"].gates == ()


@pytest.mark.parametrize(
    ("price", "actual_n", "require_full"),
    [
        (109.9, 60, False),
        (110.0, 60, False),
        (110.1, 60, False),
        (116.9, 60, False),
        (117.0, 60, False),
        (117.1, 60, False),
        (110.0, 20, False),
        (110.0, 20, True),
        (117.0, 20, True),
    ],
)
def test_peak_valley_builtin_matches_existing_strategy(
    price: float, actual_n: int, require_full: bool
) -> None:
    req = _request(price, actual_n=actual_n)
    legacy = PeakValleyStrategy(require_full_n=require_full)
    compiled = compile_recipe(
        PEAK_VALLEY_RECIPE,
        policy=EvaluationPolicy(require_full_buy_window=require_full),
    )

    assert compiled.evaluate(req) == legacy.evaluate(req)


def test_full_buy_window_metadata_matches_existing_failure() -> None:
    req = _request(110.0, actual_n=None, requested_n=None)
    compiled = compile_recipe(
        PEAK_VALLEY_RECIPE,
        policy=EvaluationPolicy(require_full_buy_window=True),
    )

    with pytest.raises(ValueError, match="actual_n and requested_n"):
        compiled.evaluate(req)
    with pytest.raises(ValueError, match="actual_n and requested_n"):
        PeakValleyStrategy(require_full_n=True).evaluate(req)


@pytest.mark.parametrize(
    ("price", "volume", "volume_n"),
    [
        (109.9, None, 7),
        (110.0, None, 7),
        (110.1, 1300.0, 7),
        (116.9, 1300.0, 7),
        (117.0, None, 7),
        (117.0, 0.0, 7),
        (117.0, 700.0, 7),
        (117.0, 1000.0, 7),
        (117.0, 1300.0, 7),
        (117.0, 1300.0, 6),
        (117.1, 1300.0, 7),
    ],
)
def test_volume_builtin_matches_existing_strategy(
    price: float, volume: float | None, volume_n: int
) -> None:
    req = _request(price, volume=volume, volume_n=volume_n)

    assert compile_recipe(PEAK_VALUE_WITH_VOLUME_RECIPE).evaluate(
        req
    ) == PeakValueWithVolumeStrategy().evaluate(req)


def test_volume_builtin_matches_full_window_policy() -> None:
    req = _request(110.0, volume=None, actual_n=20)
    compiled = compile_recipe(
        PEAK_VALUE_WITH_VOLUME_RECIPE,
        policy=EvaluationPolicy(require_full_buy_window=True),
    )
    legacy = PeakValueWithVolumeStrategy(require_full_n=True)

    assert compiled.evaluate(req) == legacy.evaluate(req)
    assert not compiled.evaluate(req).buy
    assert compiled.evaluate(req).addon


@pytest.mark.parametrize(
    ("volume", "volume_n", "expected"),
    [
        (None, 7, False),
        (0.0, 7, False),
        (1300.0, 6, False),
        (1299.9, 7, False),
        (1300.0, 7, True),
    ],
)
def test_positive_volume_gate_fails_closed(
    volume: float | None, volume_n: int, expected: bool
) -> None:
    strategy = compile_recipe(_volume_increase_recipe())
    decision = strategy.evaluate(_request(110.0, volume=volume, volume_n=volume_n))

    assert decision.buy is expected
    assert decision.addon is expected


def test_zero_volume_threshold_is_identity_without_volume() -> None:
    recipe = _volume_increase_recipe(0.0)
    result = dry_run_recipe(recipe, _request(110.0, volume=None, volume_n=0))

    assert result.decision.buy
    assert result.decision.addon
    assert result.channels["buy"].gates[1].passed
    assert result.channels["buy"].gates[1].details["identity"] is True


def test_dry_run_has_gate_traces_and_price_references() -> None:
    result = dry_run_recipe(
        PEAK_VALUE_WITH_VOLUME_RECIPE,
        _request(117.0, volume=1000.0),
    )

    assert result.decision.buy_reference.baseline_price == 100.0
    assert result.decision.buy_reference.used_coeff == 1.1
    assert result.decision.sell_reference.baseline_price == 130.0
    assert result.decision.sell_reference.used_coeff == 0.9
    assert [gate.op for gate in result.gate_traces["sell"]] == [
        "price_near_high",
        "volume_abs_change",
    ]
    assert result.channels["sell"].gates_passed is False
    assert result.channels["sell"].gates[0].passed is True
    assert result.channels["sell"].gates[1].passed is False
