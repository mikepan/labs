"""
eval.trace - Harness-agnostic data structures for evaluation traces.

All harness drivers produce these types, ensuring consistent full_trace.json
output regardless of which agent (OpenCode, pi.dev, etc.) ran the evaluation.
"""

from dataclasses import dataclass, field, asdict
from typing import Any

__all__ = [
    "ToolCallEvent",
    "TurnData",
    "StepTrace",
]


@dataclass(slots=True)
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
        return {k: v for k, v in asdict(self).items() if v is not None}


@dataclass(slots=True)
class TurnData:
    """Normalized turn data — consistent across all harness drivers."""
    tool_calls: list[ToolCallEvent] = field(default_factory=list)
    reasoning: list[str] = field(default_factory=list)
    text: list[str] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)
    tokens_in: int = 0
    tokens_out: int = 0
    peak_context_tokens: int = 0
    raw_messages: list[dict[str, Any]] = field(default_factory=list)


@dataclass(slots=True)
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
    tokens_in: int = 0
    tokens_out: int = 0
    peak_context_tokens: int = 0
    context_used_pct: float = 0.0
    used_hint: bool = False
    events: list[dict[str, Any]] = field(default_factory=list)
    evaluation: dict[str, Any] = field(default_factory=dict)
    duration_seconds: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

