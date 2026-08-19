"""Strict, discriminated operator request models for historical evaluation."""

from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator

from closing_signal.backtest.engine import BacktestConfig
from closing_signal.backtest.experiment import MetricSelector
from closing_signal.backtest.walk_forward import WalkForwardConfig


class SingleBacktestRequest(BaseModel):
    """One explicitly labeled research or out-of-sample evaluation."""

    version: str = Field(min_length=1)
    mode: Literal["single"]
    strategy_id: str = Field(min_length=1)
    output_directory: Path
    configuration: BacktestConfig
    model_config = ConfigDict(frozen=True, extra="forbid")


class WalkForwardBacktestRequest(BaseModel):
    """Candidate selection and untouched test evaluation across chronological folds."""

    version: str = Field(min_length=1)
    mode: Literal["walk_forward"]
    strategy_id: str = Field(min_length=1)
    output_directory: Path
    configuration: BacktestConfig
    candidate_parameters: tuple[dict[str, object], ...] = Field(min_length=2)
    walk_forward: WalkForwardConfig
    selection: MetricSelector
    model_config = ConfigDict(frozen=True, extra="forbid")

    @model_validator(mode="after")
    def base_segment_is_explicit(self) -> "WalkForwardBacktestRequest":
        if self.configuration.evaluation_segment != "in_sample":
            raise ValueError(
                "walk-forward base configuration must use evaluation_segment=in_sample"
            )
        return self


type BacktestRequest = SingleBacktestRequest | WalkForwardBacktestRequest

_REQUEST_ADAPTER: TypeAdapter[BacktestRequest] = TypeAdapter(
    Annotated[BacktestRequest, Field(discriminator="mode")]
)


def parse_backtest_request(value: object) -> BacktestRequest:
    """Validate an operator-controlled JSON payload without permissive fallbacks."""
    return _REQUEST_ADAPTER.validate_python(value)
