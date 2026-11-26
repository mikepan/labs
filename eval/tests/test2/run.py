import os
import re
import subprocess
from itertools import zip_longest

from eval.tests.framework import (
    Step,
    Test,
    check_kotlin_syntax,
    custom_check,
    git_changes,
    lang_detect,
)

MIN_SOURCE_LINES = 900  # A shredded source.kt (e.g. truncated to a stub) must not score later steps.

# String/char literals are matched first so comment markers inside them are ignored.
_KOTLIN_LITERAL_OR_COMMENT = re.compile(
    r'("""[\s\S]*?"""|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\')'
    r'|(//[^\n]*|/\*[\s\S]*?\*/)'
)
_KOTLIN_CODE_TOKEN = re.compile(
    r'"""(?:[\s\S]*?)"""|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\''
    r'|[A-Za-z_][A-Za-z0-9_]*|\d[\w.]*|[^\s]'
)

# ==============================================================================
# Kotlin Source Helpers
# ==============================================================================

def _strip_comments(content: str) -> str:
    """Return the file's code with comments removed, preserving string literals."""
    return _KOTLIN_LITERAL_OR_COMMENT.sub(lambda m: m.group(1) or "", content)


def _code_tokens(content: str) -> list[str]:
    """Tokenize code so formatting-only edits compare equal while real edits do not."""
    return _KOTLIN_CODE_TOKEN.findall(_strip_comments(content))


def _read_source(workspace_dir: str) -> str | None:
    source_path = os.path.join(workspace_dir, "source.kt")
    if not os.path.exists(source_path):
        return None
    with open(source_path, "r", encoding="utf-8", errors="ignore") as f:
        return f.read()


def _head_source(workspace_dir: str) -> str | None:
    """Return source.kt as committed at the start of the current step, if available."""
    res = subprocess.run(
        ["git", "show", "HEAD:source.kt"],
        cwd=workspace_dir,
        capture_output=True,
        text=True,
    )
    return res.stdout if res.returncode == 0 else None


# ==============================================================================
# Helper Assertions
# ==============================================================================

def check_function_in_kotlin(func_name: str):
    """Verify that a specific Kotlin function declaration exists in source.kt."""
    def _validate(workspace_dir: str) -> tuple[bool, str]:
        content = _read_source(workspace_dir)
        if content is None:
            return False, "File 'source.kt' not found in workspace."
        if not re.search(rf"\bfun\s+{re.escape(func_name)}\s*\(", _strip_comments(content)):
            return False, f"Function declaration 'fun {func_name}(...)' was not found in source.kt."
        return True, f"Function '{func_name}' confirmed in source.kt."
    return custom_check(_validate)


def check_pattern_in_kotlin(pattern: str, description: str):
    """Verify that a specific code pattern exists in source.kt."""
    def _validate(workspace_dir: str) -> tuple[bool, str]:
        content = _read_source(workspace_dir)
        if content is None:
            return False, "File 'source.kt' not found in workspace."
        if not re.search(pattern, _strip_comments(content)):
            return False, f"Pattern '{description}' was not found in source.kt."
        return True, f"Pattern '{description}' confirmed in source.kt."
    return custom_check(_validate)


def check_no_comments_in_kotlin(filepath: str = "source.kt"):
    """Verify that all comments (// and /* ... */) have been completely stripped from the Kotlin file."""
    def _validate(workspace_dir: str) -> tuple[bool, str]:
        target_path = os.path.join(workspace_dir, filepath)
        if not os.path.exists(target_path):
            return False, f"File '{filepath}' not found in workspace."
        with open(target_path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()

        comments_found = [
            m.group(2).strip()
            for m in _KOTLIN_LITERAL_OR_COMMENT.finditer(content)
            if m.group(2)
        ]
        if comments_found:
            preview = comments_found[:3]
            return False, f"Found {len(comments_found)} unstripped comment(s) in '{filepath}'. Examples: {preview}"
        return True, f"All comments successfully stripped from '{filepath}'."
    return custom_check(_validate)


def check_source_min_lines(min_lines: int = MIN_SOURCE_LINES):
    """Guard against a step scoring on a truncated or empty source.kt."""
    def _validate(workspace_dir: str) -> tuple[bool, str]:
        content = _read_source(workspace_dir)
        if content is None:
            return False, "File 'source.kt' not found in workspace."
        lines = len(content.splitlines())
        if lines < min_lines:
            return False, f"source.kt has only {lines} lines (expected at least {min_lines}); the file appears truncated."
        return True, f"source.kt has {lines} lines."
    return custom_check(_validate)


def check_only_comments_removed():
    """Verify source.kt differs from the step's starting version only by removed comments."""
    def _validate(workspace_dir: str) -> tuple[bool, str]:
        content = _read_source(workspace_dir)
        if content is None:
            return False, "File 'source.kt' not found in workspace."
        baseline = _head_source(workspace_dir)
        if baseline is None:
            return False, "Could not read the step's starting source.kt from git HEAD."
        if _code_tokens(content) != _code_tokens(baseline):
            return False, "source.kt contains code changes beyond comment removal (token stream differs from the step's starting version)."
        return True, "source.kt matches the starting code after comment removal."
    return custom_check(_validate)


def check_code_changed(min_tokens: int = 5):
    """Require a real code change from the step's starting version; whitespace-only edits do not count."""
    def _validate(workspace_dir: str) -> tuple[bool, str]:
        content = _read_source(workspace_dir)
        if content is None:
            return False, "File 'source.kt' not found in workspace."
        baseline = _head_source(workspace_dir)
        if baseline is None:
            return False, "Could not read the step's starting source.kt from git HEAD."
        changed = sum(1 for a, b in zip_longest(_code_tokens(baseline), _code_tokens(content)) if a != b)
        if changed < min_tokens:
            return False, f"source.kt only changes {changed} code token(s) (minimum {min_tokens}); whitespace or comment edits do not count as a refactor."
        return True, f"source.kt changes {changed} code token(s)."
    return custom_check(_validate)


# ==============================================================================
# Multi-Step Evaluation Test
# ==============================================================================

TEST = Test(
    name="kotlin_code",
    setup=[],
    steps=[
        Step(
            prompt="""Please review source.kt and create a concise 1-2 paragraph architectural and functional summary of this Android Camera application in 'summary.md'.""",
            checks=[
                git_changes("summary.md", "A", total_lines=(1, 40)),
                lang_detect("summary.md", lang="en"),
            ],
            point=2,
        ),
        Step(
            prompt="""In source.kt, add a helper function formatShutterSpeed(exposureTimeNs: Long): String to convert exposure time in nanoseconds to a camera shutter speed string (for example, "1/30s", "1/1000s", or fractional seconds like "0.5s"). Ensure source.kt maintains valid Kotlin syntax.""",
            checks=[
                git_changes("source.kt", "M", diff_lines=(4, 80)),
                check_kotlin_syntax("source.kt"),
                check_function_in_kotlin("formatShutterSpeed"),
            ],
            point=2,
        ),
        Step(
            prompt="""In source.kt, add an enum class FlashMode with values AUTO, ON, OFF, and TORCH. Then add a flashMode state variable initialized to FlashMode.AUTO inside MainScreen.""",
            checks=[
                git_changes("source.kt", "M", diff_lines=(2, 60)),
                check_kotlin_syntax("source.kt"),
                check_pattern_in_kotlin(r"enum\s+class\s+FlashMode\b", "enum class FlashMode"),
                check_pattern_in_kotlin(r"FlashMode\.AUTO", "FlashMode.AUTO reference"),
            ],
            point=2,
        ), 
        Step(
            prompt="""Clean up source.kt by removing all comments (both single-line and multi-line) from the file while keeping all code syntax intact.""",
            checks=[
                check_kotlin_syntax("source.kt"),
                check_source_min_lines(),
                check_no_comments_in_kotlin("source.kt"),
                check_only_comments_removed(),
            ],
            point=2,
        ),
        Step(
            prompt="""Refactor source.kt to improve readability, eliminate redundancies, and clean up code smells without changing any runtime functionality.""",
            checks=[
                git_changes("source.kt", "M", diff_lines=(2, 2500)),
                check_kotlin_syntax("source.kt"),
                check_source_min_lines(),
                check_code_changed(),
            ],
            point=2,
        ),
    ],
)
