"""
runner.py - Execution engine for running test steps and assertions in a workspace.
"""

from dataclasses import dataclass, field
import subprocess
import time
from typing import Any

from tests.framework.assertions import BaseAssertion, CheckResult
from tests.framework.spec import Step, Test

__all__ = [
    "StepEvaluationResult",
    "TestEvaluationResult",
    "setup_workspace",
    "commit_step_workspace",
    "evaluate_step",
]


@dataclass
class StepEvaluationResult:
    step_name: str
    passed: bool
    point: int | float = 1
    score: int | float = 0
    check_results: list[CheckResult] = field(default_factory=list)
    duration_seconds: float = 0.0


@dataclass
class TestEvaluationResult:
    test_name: str
    passed: bool
    earned_score: int | float = 0
    max_score: int | float = 0
    step_results: list[StepEvaluationResult] = field(default_factory=list)
    duration_seconds: float = 0.0



def setup_workspace(test: Test, workspace_dir: str) -> None:
    """Execute test setup commands (e.g. git init) in the workspace directory."""
    for cmd in test.setup:
        subprocess.run(cmd, shell=True, cwd=workspace_dir, check=True, capture_output=True)


def commit_step_workspace(step_name: str, workspace_dir: str) -> None:
    """Commit workspace changes after evaluating a step to ensure clean diff status for subsequent steps."""
    subprocess.run(["git", "add", "-A"], cwd=workspace_dir, check=False, capture_output=True)
    msg = f"eval-step: {step_name}"
    subprocess.run(["git", "commit", "-m", msg, "--allow-empty"], cwd=workspace_dir, check=False, capture_output=True)


def evaluate_step(step: Step, workspace_dir: str, auto_commit: bool = True) -> StepEvaluationResult:
    """Evaluate all assertions for a single step against the workspace state, then commit changes."""
    start_time = time.time()
    results: list[CheckResult] = []
    all_passed = True

    for check in step.checks:
        if isinstance(check, BaseAssertion):
            res = check.evaluate(workspace_dir)
        elif callable(check):
            try:
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
    step_point = getattr(step, "point", 1)
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


