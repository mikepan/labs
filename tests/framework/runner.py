"""
runner.py - Execution engine for running test steps and assertions in a workspace.
"""

from dataclasses import dataclass, field
import os
import subprocess
import time
from typing import Any

from tests.framework.assertions import BaseAssertion, CheckResult
from tests.framework.spec import Step, Test
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



import shlex

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
            try:
                res = check.evaluate(workspace_dir, allowed_files=expected_step_files, response=response)
            except TypeError:
                res = check.evaluate(workspace_dir, allowed_files=expected_step_files)
        elif callable(check):
            try:
                try:
                    r = check(workspace_dir, response)
                except TypeError:
                    r = check(workspace_dir)
                res = CheckResult(True, "OK") if (r is None or r is True) else CheckResult(False, "Check failed")
            except Exception as e:
                res = CheckResult(False, str(e))
        else:
            res = CheckResult(False, f"Unknown check type: {type(check)}")

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
