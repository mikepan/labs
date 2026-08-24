"""
spec.py - Minimal, declarative data structures for defining multi-step agent evaluation tests.
"""

from dataclasses import dataclass, field
from typing import Any, Callable

__all__ = ["Step", "Test"]


@dataclass
class Step:
    """A single sequential step in an agent evaluation test."""
    prompt: str
    checks: list[Any] = field(default_factory=list)
    name: str = ""
    point: int | float = 1
    timeout_minutes: int | float | None = None



@dataclass
class Test:
    """A multi-step evaluation test specification."""
    name: str
    steps: list[Step] = field(default_factory=list)
    description: str = ""
    setup: list[str] = field(default_factory=list)

