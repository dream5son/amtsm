"""Compiler and evaluator for validated signal recipes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from pydantic import BaseModel

from app.market_signal.operators.base import OperatorSpec
from app.market_signal.operators.registry import DEFAULT_OPERATOR_REGISTRY
from app.market_signal.recipe_schema import (
    OperatorCall,
    RecipeInput,
    StrategyRecipeV1,
    validate_recipe,
)
from app.market_signal.strategy import (
    MarketSignalStrategy,
    SignalDecision,
    SignalReference,
    SignalRequest,
)


@dataclass(frozen=True)
class EvaluationPolicy:
    require_full_buy_window: bool = False


@dataclass(frozen=True)
class FeedRequirements:
    price_lookback_days: int
    volume_lookback_days: int | None = None
    requires_current_volume: bool = False

    @property
    def requires_volume(self) -> bool:
        return self.requires_current_volume


@dataclass(frozen=True)
class GateTrace:
    channel: str
    index: int
    op: str
    version: int
    params: Mapping[str, Any]
    passed: bool
    details: Mapping[str, Any]


@dataclass(frozen=True)
class ChannelTrace:
    present: bool
    passed: bool
    gates_passed: bool
    policy_passed: bool
    gates: tuple[GateTrace, ...]


@dataclass(frozen=True)
class DryRunResult:
    decision: SignalDecision
    channels: Mapping[str, ChannelTrace]

    @property
    def traces(self) -> Mapping[str, ChannelTrace]:
        return self.channels

    @property
    def gate_traces(self) -> Mapping[str, tuple[GateTrace, ...]]:
        return {
            channel: trace.gates
            for channel, trace in self.channels.items()
        }


@dataclass(frozen=True)
class _CompiledGate:
    call: OperatorCall
    spec: OperatorSpec
    params: BaseModel


def _all_calls(recipe: StrategyRecipeV1) -> tuple[OperatorCall, ...]:
    calls = [*recipe.channels.buy.all, *recipe.channels.sell.all]
    if recipe.channels.addon is not None:
        calls.extend(recipe.channels.addon.all)
    return tuple(calls)


def analyze_recipe(recipe: RecipeInput) -> FeedRequirements:
    validated = validate_recipe(recipe)
    calls = _all_calls(validated)
    price_lookback = next(
        int(call.params["lookback_days"])
        for call in calls
        if call.op in {"price_near_low", "price_near_high"}
    )

    active_volume_calls = [
        call
        for call in calls
        if (
            call.op == "volume_increase"
            and float(call.params["min_change_pct"]) > 0
        )
        or (
            call.op == "volume_abs_change"
            and float(call.params["min_abs_change_pct"]) > 0
        )
    ]
    volume_lookback = (
        int(active_volume_calls[0].params["lookback_days"])
        if active_volume_calls
        else None
    )
    return FeedRequirements(
        price_lookback_days=price_lookback,
        volume_lookback_days=volume_lookback,
        requires_current_volume=bool(active_volume_calls),
    )


def _compile_gate(call: OperatorCall) -> _CompiledGate:
    spec = DEFAULT_OPERATOR_REGISTRY[f"{call.op}@{call.version}"]
    return _CompiledGate(
        call=call,
        spec=spec,
        params=spec.validate_params(call.params),
    )


class CompiledRecipeStrategy(MarketSignalStrategy):
    def __init__(
        self,
        recipe: StrategyRecipeV1,
        policy: EvaluationPolicy,
    ) -> None:
        self.recipe = recipe
        self.policy = policy
        self.requirements = analyze_recipe(recipe)
        self._buy = tuple(_compile_gate(call) for call in recipe.channels.buy.all)
        self._sell = tuple(_compile_gate(call) for call in recipe.channels.sell.all)
        self._addon = (
            tuple(_compile_gate(call) for call in recipe.channels.addon.all)
            if recipe.channels.addon is not None
            else None
        )

    @property
    def require_full_n(self) -> bool:
        return self.policy.require_full_buy_window

    def _run_channel(
        self,
        channel: str,
        gates: tuple[_CompiledGate, ...] | None,
        req: SignalRequest,
        *,
        policy_passed: bool = True,
    ) -> ChannelTrace:
        if gates is None:
            return ChannelTrace(
                present=False,
                passed=False,
                gates_passed=False,
                policy_passed=policy_passed,
                gates=(),
            )

        traces: list[GateTrace] = []
        for index, gate in enumerate(gates):
            verdict = gate.spec.evaluate(req, gate.params)
            traces.append(
                GateTrace(
                    channel=channel,
                    index=index,
                    op=gate.call.op,
                    version=gate.call.version,
                    params=gate.call.params,
                    passed=verdict.passed,
                    details=verdict.details,
                )
            )
        gates_passed = all(trace.passed for trace in traces)
        return ChannelTrace(
            present=True,
            passed=gates_passed and policy_passed,
            gates_passed=gates_passed,
            policy_passed=policy_passed,
            gates=tuple(traces),
        )

    def _buy_policy_passed(self, req: SignalRequest) -> bool:
        if not self.policy.require_full_buy_window:
            return True
        window = req.feed.baseline()
        if window.actual_n is None or window.requested_n is None:
            raise ValueError(
                "actual_n and requested_n are required when "
                "require_full_buy_window=True"
            )
        return window.actual_n >= window.requested_n

    def dry_run(self, req: SignalRequest) -> DryRunResult:
        buy = self._run_channel(
            "buy",
            self._buy,
            req,
            policy_passed=self._buy_policy_passed(req),
        )
        sell = self._run_channel("sell", self._sell, req)
        addon = self._run_channel("addon", self._addon, req)

        buy_reference = self._reference(buy)
        sell_reference = self._reference(sell)
        decision = SignalDecision(
            buy=buy.passed,
            sell=sell.passed,
            addon=addon.passed,
            buy_reference=buy_reference,
            sell_reference=sell_reference,
        )
        return DryRunResult(
            decision=decision,
            channels={"buy": buy, "sell": sell, "addon": addon},
        )

    def evaluate(self, req: SignalRequest) -> SignalDecision:
        return self.dry_run(req).decision

    @staticmethod
    def _reference(trace: ChannelTrace) -> SignalReference:
        first = trace.gates[0]
        return SignalReference(
            baseline_price=float(first.details["baseline_price"]),
            used_coeff=float(first.details["factor"]),
        )


def compile_recipe(
    recipe: RecipeInput,
    *,
    policy: EvaluationPolicy | None = None,
) -> CompiledRecipeStrategy:
    return CompiledRecipeStrategy(
        validate_recipe(recipe),
        policy or EvaluationPolicy(),
    )


def dry_run_recipe(
    recipe: RecipeInput,
    req: SignalRequest,
    *,
    policy: EvaluationPolicy | None = None,
) -> DryRunResult:
    return compile_recipe(recipe, policy=policy).dry_run(req)


__all__ = [
    "ChannelTrace",
    "CompiledRecipeStrategy",
    "DryRunResult",
    "EvaluationPolicy",
    "FeedRequirements",
    "GateTrace",
    "analyze_recipe",
    "compile_recipe",
    "dry_run_recipe",
]
