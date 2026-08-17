"""
runner.py - Execution engine for running test steps and assertions in a workspace.
"""

from dataclasses import dataclass, field
import subprocess
import time
from typing import Any

from tests.framework.assertions import BaseAssertion, CheckResult
from tests.framework.spec import Step, Test


@dataclass
class StepEvaluationResult:
    step_name: str
    passed: bool
    check_results: list[CheckResult] = field(default_factory=list)
    duration_seconds: float = 0.0


@dataclass
class TestEvaluationResult:
    test_name: str
    passed: bool
    step_results: list[StepEvaluationResult] = field(default_factory=list)
    duration_seconds: float = 0.0


def setup_workspace(test: Test, workspace_dir: str) -> None:
    """Execute test setup commands (e.g. git init) in the workspace directory."""
    for cmd in test.setup:
        subprocess.run(cmd, shell=True, cwd=workspace_dir, check=True, capture_output=True)


def evaluate_step(step: Step, workspace_dir: str) -> StepEvaluationResult:
    """Evaluate all assertions for a single step against the workspace state."""
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

    elapsed = round(time.time() - start_time, 3)
    return StepEvaluationResult(
        step_name=step.name or f"Step ({step.prompt[:30]}...)",
        passed=all_passed,
        check_results=results,
        duration_seconds=elapsed,
    )
