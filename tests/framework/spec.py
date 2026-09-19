"""
spec.py - Minimal, declarative data structures for defining multi-step agent evaluation tests.
"""

from dataclasses import dataclass, field
from typing import Any

from eval.config import DEFAULT_STEP_POINT

__all__ = ["Step", "Test"]


@dataclass
class Step:
    """A single sequential step in an agent evaluation test."""
    prompt: str
    checks: list[Any] = field(default_factory=list)
    name: str = ""
    point: int | float = DEFAULT_STEP_POINT
    timeout_minutes: int | float | None = None
    timeout_seconds: int | float | None = None
    hint: str | None = None



@dataclass
class Test:
    """A multi-step evaluation test specification."""
    name: str
    steps: list[Step] = field(default_factory=list)
    setup: list[str] = field(default_factory=list)
    host_eval: bool = False
    timeout_seconds: int | float | None = None


