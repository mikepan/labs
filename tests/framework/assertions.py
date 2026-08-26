"""
assertions.py - Concise assertion primitives for evaluating agent actions in git repositories.
"""

from dataclasses import dataclass
import json
import os
import re
import subprocess
from typing import Any, Callable


__all__ = [
    "CheckResult",
    "BaseAssertion",
    "GitChangeAssert",
    "LangDetectAssert",
    "FilesIdenticalAssert",
    "KotlinSyntaxAssert",
    "CustomAssert",
    "git_changes",
    "lang_detect",
    "files_identical",
    "check_kotlin_syntax",
    "custom_check",
]


@dataclass
class CheckResult:
    passed: bool
    message: str
    details: dict[str, Any] | None = None


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
        total_lines: tuple[int, int] | int | None = None,
        diff_lines: tuple[int, int] | int | None = None,
        allow_extra: bool = False,
    ):
        self.filepath = filepath
        self.status = status.strip().upper() if status else "A"
        self.total_lines = total_lines
        self.diff_lines = diff_lines
        self.allow_extra = allow_extra

    def evaluate(self, workspace_dir: str, allowed_files: set[str] | None = None) -> CheckResult:
        full_path = os.path.join(workspace_dir, self.filepath)

        # 0. Verify workspace is a valid git repository
        git_check = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=workspace_dir,
            capture_output=True,
            text=True,
        )
        if git_check.returncode != 0:
            return CheckResult(
                False,
                f"Workspace '{workspace_dir}' is not a valid git repository ('.git' directory was removed or damaged).",
            )

        # 1. Check for unexpected extra files when allow_extra is False
        if not self.allow_extra:
            status_res = subprocess.run(
                ["git", "status", "--porcelain", "-uall"],
                cwd=workspace_dir,
                capture_output=True,
                text=True,
            )
            if status_res.returncode == 0 and status_res.stdout:
                expected_paths = {os.path.normpath(f) for f in (allowed_files or {self.filepath})}
                changed_files = set()
                for line in status_res.stdout.splitlines():
                    line = line.strip()
                    if not line or len(line) < 3:
                        continue
                    path_part = line[2:].strip()
                    if " -> " in path_part:
                        old_p, new_p = path_part.split(" -> ", 1)
                        changed_files.add(os.path.normpath(old_p.strip('"\' ')))
                        changed_files.add(os.path.normpath(new_p.strip('"\' ')))
                    else:
                        changed_files.add(os.path.normpath(path_part.strip('"\' ')))

                extra_files = changed_files - expected_paths
                if extra_files:
                    return CheckResult(
                        False,
                        f"Unexpected extra modified or untracked file(s) found in workspace: {sorted(extra_files)} (allow_extra=False).",
                        {"extra_files": sorted(extra_files), "allowed_files": sorted(expected_paths)},
                    )

        # Normalize status code (support "A", "ADD", "M", "MODIFY", "D", "DELETE")
        is_delete = self.status in ("D", "DELETE")

        # 2. Check file existence according to status
        if is_delete:
            if os.path.exists(full_path):
                return CheckResult(
                    False,
                    f"Expected file '{self.filepath}' to be deleted, but it still exists in workspace '{workspace_dir}'.",
                )
        else:
            if not os.path.exists(full_path):
                available_files = [f for f in os.listdir(workspace_dir) if not f.startswith(".")]
                return CheckResult(
                    False,
                    f"Expected file '{self.filepath}' to exist, but was not found. (Files present in workspace: {available_files})",
                )

        # 3. Check total lines count if specified
        line_count = 0
        if os.path.exists(full_path):
            with open(full_path, "r", encoding="utf-8", errors="ignore") as f:
                line_count = len(f.readlines())

        if self.total_lines is not None:
            min_lines, max_lines = self.total_lines if isinstance(self.total_lines, tuple) else (self.total_lines, self.total_lines)
            if not (min_lines <= line_count <= max_lines):
                return CheckResult(
                    False,
                    f"File '{self.filepath}' total line count {line_count} is out of expected range [{min_lines}, {max_lines}].",
                    {"line_count": line_count, "expected_range": (min_lines, max_lines)},
                )

        # 3. Check git diff / numstat lines changed if diff_lines specified
        if self.diff_lines is not None:
            res = subprocess.run(
                ["git", "diff", "--numstat", "HEAD", "--", self.filepath],
                cwd=workspace_dir,
                capture_output=True,
                text=True,
            )
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
                    f"File '{self.filepath}' diff lines changed {changed_lines} (+{added}/-{deleted}) is out of expected range [{min_diff}, {max_diff}].",
                    {"changed_lines": changed_lines, "added": added, "deleted": deleted, "expected_range": (min_diff, max_diff)},
                )

        return CheckResult(True, f"Git change verified for '{self.filepath}' (total lines: {line_count}).")


class LangDetectAssert(BaseAssertion):
    """Asserts that a file's content matches the expected natural language."""

    def __init__(self, filepath: str, lang: str = "en"):
        self.filepath = filepath
        self.lang = lang.lower()

    def evaluate(self, workspace_dir: str) -> CheckResult:
        full_path = os.path.join(workspace_dir, self.filepath)
        if not os.path.exists(full_path):
            return CheckResult(False, f"File '{self.filepath}' not found for language detection.")

        with open(full_path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read().strip()

        if not content:
            return CheckResult(False, f"File '{self.filepath}' is empty.")

        # Language detection: try langdetect library, fallback to unicode heuristics
        detected_lang = None
        try:
            import langdetect

            detected_lang = langdetect.detect(content)
        except Exception:
            has_cjk = bool(re.search(r"[\u4e00-\u9fff]", content))
            if has_cjk:
                detected_lang = "zh-cn"
            else:
                detected_lang = "en"

        matched = (
            detected_lang.startswith(self.lang)
            or self.lang.startswith(detected_lang)
            or (self.lang in ["zh", "chinese", "zh-cn"] and detected_lang in ["zh-cn", "zh-tw", "ko", "ja"])
        )

        if not matched:
            preview = content[:100].replace("\n", " ")
            return CheckResult(
                False,
                f"Expected language '{self.lang}' for '{self.filepath}', but detected '{detected_lang}'. Content snippet: '{preview}'",
                {"detected": detected_lang, "expected": self.lang, "snippet": preview},
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
            return CheckResult(False, f"Source file '{self.file1}' not found in workspace.")
        if not os.path.exists(p2):
            return CheckResult(False, f"Target file '{self.file2}' not found in workspace.")

        with open(p1, "r", encoding="utf-8", errors="ignore") as f1, open(p2, "r", encoding="utf-8", errors="ignore") as f2:
            c1 = f1.read().strip()
            c2 = f2.read().strip()

        if c1 != c2:
            return CheckResult(
                False,
                f"Files '{self.file1}' (len: {len(c1)}) and '{self.file2}' (len: {len(c2)}) have different contents.",
                {"len1": len(c1), "len2": len(c2)},
            )

        return CheckResult(True, f"Files '{self.file1}' and '{self.file2}' are identical.")


class KotlinSyntaxAssert(BaseAssertion):
    """Asserts that a Kotlin file in the workspace has valid syntax (or optionally conforms to ktlint style)."""

    def __init__(self, filepath: str, check_style: bool = False):
        self.filepath = filepath
        self.check_style = check_style

    def evaluate(self, workspace_dir: str) -> CheckResult:
        full_path = os.path.join(workspace_dir, self.filepath)
        if not os.path.exists(full_path):
            return CheckResult(False, f"Kotlin file '{self.filepath}' not found in workspace '{workspace_dir}'.")

        res = subprocess.run(
            ["ktlint", "--reporter=json", self.filepath],
            cwd=workspace_dir,
            capture_output=True,
            text=True,
        )

        stdout = res.stdout
        json_match = re.search(r"(\[\s*\{.*\}\s*\]|\[\s*\])", stdout, re.DOTALL)
        if not json_match:
            if res.returncode != 0 and "Not a valid Kotlin file" in (stdout + res.stderr):
                return CheckResult(False, f"Kotlin syntax error in '{self.filepath}': {stdout.strip() or res.stderr.strip()}")
            return CheckResult(True, f"Kotlin file '{self.filepath}' passed validation.")

        try:
            data = json.loads(json_match.group(1))
            errors = data[0].get("errors", []) if data else []
        except Exception as e:
            return CheckResult(False, f"Failed to parse ktlint output for '{self.filepath}': {e}")

        syntax_errors = [
            e for e in errors
            if not e.get("rule") or "Not a valid Kotlin file" in e.get("message", "")
        ]

        if syntax_errors:
            first_err = syntax_errors[0]
            err_msg = f"Kotlin syntax error in '{self.filepath}' at line {first_err.get('line')}:{first_err.get('column')} - {first_err.get('message')}"
            return CheckResult(False, err_msg, {"syntax_errors": syntax_errors})

        if self.check_style and errors:
            first_err = errors[0]
            err_msg = f"Kotlin style violation in '{self.filepath}' at line {first_err.get('line')}:{first_err.get('column')} [{first_err.get('rule')}] - {first_err.get('message')}"
            return CheckResult(False, err_msg, {"style_violations": errors})

        return CheckResult(True, f"Kotlin syntax verified for '{self.filepath}'.")


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
    total_lines: tuple[int, int] | int | None = None,
    diff_lines: tuple[int, int] | int | None = None,
    allow_extra: bool = False,
) -> GitChangeAssert:
    return GitChangeAssert(
        filepath=filepath,
        status=status,
        total_lines=total_lines,
        diff_lines=diff_lines,
        allow_extra=allow_extra,
    )


def lang_detect(filepath: str, lang: str = "en") -> LangDetectAssert:
    return LangDetectAssert(filepath=filepath, lang=lang)


def files_identical(file1: str, file2: str) -> FilesIdenticalAssert:
    return FilesIdenticalAssert(file1=file1, file2=file2)


def check_kotlin_syntax(filepath: str, check_style: bool = False) -> KotlinSyntaxAssert:
    return KotlinSyntaxAssert(filepath=filepath, check_style=check_style)


def custom_check(func: Callable[[str], Any]) -> CustomAssert:
    return CustomAssert(func=func)
