"""
tests/framework - Lightweight, extensible testing and evaluation framework for coding agents.
"""

from .assertions import (
    BaseAssertion,
    CheckResult,
    CustomAssert,
    FilesIdenticalAssert,
    GitChangeAssert,
    LangDetectAssert,
    custom_check,
    files_identical,
    git_changes,
    lang_detect,
)
from .runner import (
    StepEvaluationResult,
    commit_step_workspace,
    evaluate_step,
)
from .spec import Step, Test

__all__ = [
    # Spec
    "Step",
    "Test",
    # Assertions
    "BaseAssertion",
    "CheckResult",
    "GitChangeAssert",
    "LangDetectAssert",
    "FilesIdenticalAssert",
    "CustomAssert",
    "git_changes",
    "lang_detect",
    "files_identical",
    "custom_check",
    # Runner
    "StepEvaluationResult",
    "commit_step_workspace",
    "evaluate_step",
]
