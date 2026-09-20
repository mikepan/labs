"""
runner.py - Execution engine for running test steps and assertions in a workspace.
"""

from dataclasses import dataclass, field
import os
import shlex
import subprocess
import time
from typing import Any

from .assertions import BaseAssertion, CheckResult, CustomAssert
from .spec import Step, Test
from eval.config import DEFAULT_STEP_POINT

__all__ = [
    "StepEvaluationResult",
    "commit_step_workspace",
    "evaluate_step",
]


@dataclass
class StepEvaluationResult:
    step_name: str
    passed: bool
    point: int | float = DEFAULT_STEP_POINT
    score: int | float = 0
    check_results: list[CheckResult] = field(default_factory=list)
    duration_seconds: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_name": self.step_name,
            "passed": self.passed,
            "point": self.point,
            "score": self.score,
            "duration_seconds": self.duration_seconds,
            "check_results": [
                {"passed": c.passed, "message": c.message, "details": c.details}
                for c in self.check_results
            ],
        }


def commit_step_workspace(step_name: str, workspace_dir: str) -> bool:
    """Commit workspace changes after evaluating a step to ensure clean diff status for subsequent steps."""
    cmd = f"git add -A && git commit -m {shlex.quote(f'step: {step_name}')} --allow-empty"
    res = subprocess.run(["bash", "-c", cmd], cwd=workspace_dir, check=False, capture_output=True)
    return res.returncode == 0


def evaluate_step(step: Step, workspace_dir: str, auto_commit: bool = True, response: str | None = None) -> StepEvaluationResult:
    """Evaluate all assertions for a single step against the workspace state, then commit changes."""
    start_time = time.time()
    results: list[CheckResult] = []
    all_passed = True

    # Collect all expected file paths declared in GitChangeAssert checks for this step
    expected_step_files = {
        getattr(c, "filepath") for c in step.checks if hasattr(c, "filepath") and getattr(c, "filepath")
    }

    for check in step.checks:
        if isinstance(check, BaseAssertion):
            assertion = check
        elif callable(check):
            assertion = CustomAssert(check)
        else:
            results.append(CheckResult(False, f"Unknown check type: {type(check)}"))
            all_passed = False
            continue

        try:
            res = assertion.evaluate(workspace_dir, allowed_files=expected_step_files, response=response)
        except TypeError:
            res = assertion.evaluate(workspace_dir, allowed_files=expected_step_files)
        except Exception as e:
            res = CheckResult(False, str(e))

        results.append(res)
        if not res.passed:
            all_passed = False

    step_title = step.name or f"Step ({step.prompt[:30]}...)"
    step_point = step.point
    earned_score = step_point if all_passed else 0

    if auto_commit:
        commit_step_workspace(step_title, workspace_dir)

    elapsed = round(time.time() - start_time, 3)
    return StepEvaluationResult(
        step_name=step_title,
        passed=all_passed,
        point=step_point,
        score=earned_score,
        check_results=results,
        duration_seconds=elapsed,
    )
