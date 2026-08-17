"""
tests/framework - Lightweight, extensible testing and evaluation framework for coding agents.
"""

from tests.framework.assertions import (
    ADD,
    DELETE,
    GIT_FILE_ADD,
    GIT_FILE_REMOVE,
    GIT_FILE_UPDATE,
    MODIFY,
    BaseAssertion,
    CheckResult,
    CustomAssert,
    FilesIdenticalAssert,
    GitChangeAssert,
    HtmlValidAssert,
    LangDetectAssert,
    custom_check,
    files_identical,
    gibberish_detect,
    git_changes,
    html_valid,
    lang_detect,
)
from tests.framework.runner import (
    StepEvaluationResult,
    TestEvaluationResult,
    evaluate_step,
    setup_workspace,
)
from tests.framework.spec import Step, Test

__all__ = [
    "Test",
    "Step",
    "ADD",
    "MODIFY",
    "DELETE",
    "GIT_FILE_ADD",
    "GIT_FILE_UPDATE",
    "GIT_FILE_REMOVE",
    "git_changes",
    "lang_detect",
    "gibberish_detect",
    "html_valid",
    "files_identical",
    "custom_check",
    "BaseAssertion",
    "CheckResult",
    "GitChangeAssert",
    "LangDetectAssert",
    "HtmlValidAssert",
    "FilesIdenticalAssert",
    "CustomAssert",
    "evaluate_step",
    "setup_workspace",
    "StepEvaluationResult",
    "TestEvaluationResult",
]
