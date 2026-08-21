"""
assertions.py - Concise assertion primitives for evaluating agent actions in git repositories.
"""

from dataclasses import dataclass
from enum import Enum
import os
import re
import subprocess
from typing import Any, Callable, Optional


__all__ = [
    "CheckResult",
    "BaseAssertion",
    "GitChangeAssert",
    "LangDetectAssert",
    "FilesIdenticalAssert",
    "CustomAssert",
    "git_changes",
    "lang_detect",
    "gibberish_detect",
    "files_identical",
    "custom_check",
]


@dataclass
class CheckResult:
    passed: bool
    message: str
    details: Optional[dict[str, Any]] = None


class BaseAssertion:
    """Base class for all step assertions."""

    def evaluate(self, workspace_dir: str) -> CheckResult:
        raise NotImplementedError


class GitChangeAssert(BaseAssertion):
    """Asserts that a file underwent a specific git change (add, modify, delete) with total line bounds or diff line bounds."""

    def __init__(
        self,
        filepath: str,
        status: str = "A",
        total_lines: Optional[tuple[int, int] | int] = None,
        diff_lines: Optional[tuple[int, int] | int] = None,
    ):
        self.filepath = filepath
        self.status = status.strip().upper() if status else "A"
        self.total_lines = total_lines
        self.diff_lines = diff_lines

    def evaluate(self, workspace_dir: str) -> CheckResult:
        full_path = os.path.join(workspace_dir, self.filepath)

        # Normalize status code (support "A", "ADD", "M", "MODIFY", "D", "DELETE")
        is_delete = self.status in ("D", "DELETE")

        # 1. Check file existence according to status
        if is_delete:
            if os.path.exists(full_path):
                return CheckResult(False, f"Expected file '{self.filepath}' to be deleted, but it exists.")
        else:
            if not os.path.exists(full_path):
                return CheckResult(False, f"Expected file '{self.filepath}' to exist, but was not found.")

        # 2. Check total lines count if specified
        line_count = 0
        if os.path.exists(full_path):
            with open(full_path, "r", encoding="utf-8", errors="ignore") as f:
                line_count = len(f.readlines())

        if self.total_lines is not None:
            min_lines, max_lines = self.total_lines if isinstance(self.total_lines, tuple) else (self.total_lines, self.total_lines)
            if not (min_lines <= line_count <= max_lines):
                return CheckResult(
                    False,
                    f"File '{self.filepath}' total line count {line_count} out of expected range [{min_lines}, {max_lines}].",
                    {"line_count": line_count, "range": (min_lines, max_lines)},
                )

        # 3. Check git diff / numstat lines changed if diff_lines specified
        if self.diff_lines is not None:
            res = subprocess.run(
                ["git", "diff", "--numstat", "HEAD", "--", self.filepath],
                cwd=workspace_dir,
                capture_output=True,
                text=True,
            )
            # If nothing in HEAD diff, try uncommitted / staged diff
            numstat = res.stdout.strip()
            if not numstat:
                res_uncommitted = subprocess.run(
                    ["git", "diff", "--numstat", "--", self.filepath],
                    cwd=workspace_dir,
                    capture_output=True,
                    text=True,
                )
                numstat = res_uncommitted.stdout.strip()

            added = 0
            deleted = 0
            if numstat:
                parts = numstat.split()
                if len(parts) >= 2:
                    added = int(parts[0]) if parts[0].isdigit() else 0
                    deleted = int(parts[1]) if parts[1].isdigit() else 0

            changed_lines = added + deleted
            min_diff, max_diff = self.diff_lines if isinstance(self.diff_lines, tuple) else (self.diff_lines, self.diff_lines)
            if not (min_diff <= changed_lines <= max_diff):
                return CheckResult(
                    False,
                    f"File '{self.filepath}' diff lines changed {changed_lines} (+{added}/-{deleted}) out of expected range [{min_diff}, {max_diff}].",
                    {"changed_lines": changed_lines, "added": added, "deleted": deleted, "range": (min_diff, max_diff)},
                )

        return CheckResult(True, f"Git change verified for '{self.filepath}' (total: {line_count} lines).")


class LangDetectAssert(BaseAssertion):
    """Asserts that a file's content matches the expected natural language."""

    def __init__(self, filepath: str, lang: str = "en", no_gibberish: bool = True):
        self.filepath = filepath
        self.lang = lang.lower()
        self.no_gibberish = no_gibberish

    def evaluate(self, workspace_dir: str) -> CheckResult:
        full_path = os.path.join(workspace_dir, self.filepath)
        if not os.path.exists(full_path):
            return CheckResult(False, f"File '{self.filepath}' not found for language detection.")

        with open(full_path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read().strip()

        if not content:
            return CheckResult(False, f"File '{self.filepath}' is empty.")

        # Basic gibberish check
        if self.no_gibberish:
            # Check ratio of readable text vs repeated characters
            if len(content) > 20 and len(set(content)) < 5:
                return CheckResult(False, f"Gibberish detected in '{self.filepath}': low character variety.")

        # Language detection: try langdetect library, fallback to unicode heuristics
        detected_lang = None
        try:
            import langdetect

            detected_lang = langdetect.detect(content)
        except Exception:
            # Fallback: check CJK unicode for chinese, latin for english
            has_cjk = bool(re.search(r"[\u4e00-\u9fff]", content))
            if has_cjk:
                detected_lang = "zh-cn"
            else:
                detected_lang = "en"

        # Normalize comparison (e.g. 'zh' vs 'zh-cn' vs 'zh-tw')
        matched = (
            detected_lang.startswith(self.lang)
            or self.lang.startswith(detected_lang)
            or (self.lang in ["zh", "chinese", "zh-cn"] and detected_lang in ["zh-cn", "zh-tw", "ko", "ja"])
        )

        if not matched:
            return CheckResult(
                False,
                f"Expected language '{self.lang}' for '{self.filepath}', but detected '{detected_lang}'.",
                {"detected": detected_lang, "expected": self.lang},
            )

        return CheckResult(True, f"Language '{self.lang}' confirmed for '{self.filepath}'.")


class FilesIdenticalAssert(BaseAssertion):
    """Asserts that two files in the workspace have identical contents."""

    def __init__(self, file1: str, file2: str):
        self.file1 = file1
        self.file2 = file2

    def evaluate(self, workspace_dir: str) -> CheckResult:
        p1 = os.path.join(workspace_dir, self.file1)
        p2 = os.path.join(workspace_dir, self.file2)

        if not os.path.exists(p1):
            return CheckResult(False, f"File '{self.file1}' not found.")
        if not os.path.exists(p2):
            return CheckResult(False, f"File '{self.file2}' not found.")

        with open(p1, "r", encoding="utf-8", errors="ignore") as f1, open(p2, "r", encoding="utf-8", errors="ignore") as f2:
            c1 = f1.read().strip()
            c2 = f2.read().strip()

        if c1 != c2:
            return CheckResult(False, f"Files '{self.file1}' and '{self.file2}' are not identical.")

        return CheckResult(True, f"Files '{self.file1}' and '{self.file2}' are identical.")


class CustomAssert(BaseAssertion):
    """Wraps a custom python callable assertion."""

    def __init__(self, func: Callable[[str], bool | tuple[bool, str] | None]):
        self.func = func

    def evaluate(self, workspace_dir: str) -> CheckResult:
        try:
            res = self.func(workspace_dir)
            if res is None or res is True:
                return CheckResult(True, f"Custom check '{self.func.__name__}' passed.")
            if res is False:
                return CheckResult(False, f"Custom check '{self.func.__name__}' failed.")
            if isinstance(res, tuple):
                passed, msg = res
                return CheckResult(passed, msg)
            return CheckResult(True, "OK")
        except AssertionError as e:
            return CheckResult(False, f"AssertionError in '{self.func.__name__}': {e}")
        except Exception as e:
            return CheckResult(False, f"Exception in custom check '{self.func.__name__}': {e}")


# Concise factory functions for test definitions
def git_changes(
    filepath: str,
    status: str = "A",
    total_lines: Optional[tuple[int, int] | int] = None,
    diff_lines: Optional[tuple[int, int] | int] = None,
) -> GitChangeAssert:
    return GitChangeAssert(
        filepath=filepath,
        status=status,
        total_lines=total_lines,
        diff_lines=diff_lines,
    )


def lang_detect(filepath: str, lang: str = "en", no_gibberish: bool = True) -> LangDetectAssert:
    return LangDetectAssert(filepath=filepath, lang=lang, no_gibberish=no_gibberish)


def gibberish_detect(filepath: str) -> LangDetectAssert:
    return LangDetectAssert(filepath=filepath, no_gibberish=True)


def files_identical(file1: str, file2: str) -> FilesIdenticalAssert:
    return FilesIdenticalAssert(file1=file1, file2=file2)


def custom_check(func: Callable[[str], Any]) -> CustomAssert:
    return CustomAssert(func=func)

