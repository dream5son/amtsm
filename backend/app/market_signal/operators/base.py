"""Typed operator contracts used by the signal recipe compiler."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel

from app.market_signal.strategy import SignalReference, SignalRequest

OperatorEvaluator = Callable[[SignalRequest, BaseModel], "OperatorVerdict"]


@dataclass(frozen=True)
class OperatorVerdict:
    passed: bool
    details: Mapping[str, Any] = field(default_factory=dict)
    reference: SignalReference | None = None


@dataclass(frozen=True)
class OperatorSpec:
    op: str
    version: int
    params_model: type[BaseModel]
    channels: frozenset[str]
    evaluator: OperatorEvaluator

    @property
    def key(self) -> str:
        return f"{self.op}@{self.version}"

    def validate_params(self, params: Mapping[str, Any]) -> BaseModel:
        return self.params_model.model_validate(params)

    def evaluate(self, req: SignalRequest, params: BaseModel) -> OperatorVerdict:
        return self.evaluator(req, params)
