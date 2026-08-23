"""
eval.trace - Harness-agnostic data structures for evaluation traces.

All harness drivers produce these types, ensuring consistent full_trace.json
output regardless of which agent (OpenCode, pi.dev, etc.) ran the evaluation.
"""

from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "ToolCallEvent",
    "TurnData",
    "StepTrace",
]


@dataclass
class ToolCallEvent:
    """A single tool invocation within a turn."""
    tool: str
    timestamp: str
    call_id: str | None = None
    status: str | None = None
    input: str | None = None
    output: str | None = None
    exit_code: int | None = None
    duration_ms: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items() if v is not None}


@dataclass
class TurnData:
    """Normalized turn data — consistent across all harness drivers.

    Each driver parses its own response format but must produce a TurnData,
    guaranteeing the orchestrator and full_trace.json stay harness-agnostic.
    """
    tool_calls: list[ToolCallEvent] = field(default_factory=list)
    reasoning: list[str] = field(default_factory=list)
    text: list[str] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)
    tokens_in: int = 0
    tokens_out: int = 0
    raw_messages: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class StepTrace:
    """A single step's trace record for full_trace.json."""
    step_index: int
    step_name: str
    prompt: str
    point: int | float
    earned_score: int | float
    max_score: int | float
    start_time: str
    end_time: str
    tokens_in: int
    tokens_out: int
    events: list[dict[str, Any]]
    tool_calls: list[dict[str, Any]]
    reasoning_blocks: list[str]
    response_text: str
    messages: list[dict[str, Any]]
    evaluation: dict[str, Any]
    duration_seconds: float

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__
