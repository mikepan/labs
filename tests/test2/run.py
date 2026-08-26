import os
import re

from tests.framework import (
    Step,
    Test,
    check_kotlin_syntax,
    custom_check,
    git_changes,
    lang_detect,
)

# ==============================================================================
# Helper Assertions
# ==============================================================================

def check_function_in_kotlin(func_name: str):
    """Verify that a specific Kotlin function declaration exists in source.kt."""
    def _validate(workspace_dir: str) -> tuple[bool, str]:
        source_path = os.path.join(workspace_dir, "source.kt")
        if not os.path.exists(source_path):
            return False, "File 'source.kt' not found in workspace."
        with open(source_path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()
        if not re.search(rf"\bfun\s+{re.escape(func_name)}\s*\(", content):
            return False, f"Function declaration 'fun {func_name}(...)' was not found in source.kt."
        return True, f"Function '{func_name}' confirmed in source.kt."
    return custom_check(_validate)


def check_pattern_in_kotlin(pattern: str, description: str):
    """Verify that a specific code pattern exists in source.kt."""
    def _validate(workspace_dir: str) -> tuple[bool, str]:
        source_path = os.path.join(workspace_dir, "source.kt")
        if not os.path.exists(source_path):
            return False, "File 'source.kt' not found in workspace."
        with open(source_path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()
        if not re.search(pattern, content):
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

        # Tokenize strings/literals vs comments to avoid matching '//' inside string literals
        pattern = r'("""[\s\S]*?"""|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\')|(//[^\n]*|/\*[\s\S]*?\*/)'
        comments_found = []
        for match in re.finditer(pattern, content):
            if match.group(2):
                comments_found.append(match.group(2).strip())

        if comments_found:
            preview = comments_found[:3]
            return False, f"Found {len(comments_found)} unstripped comment(s) in '{filepath}'. Examples: {preview}"
        return True, f"All comments successfully stripped from '{filepath}'."
    return custom_check(_validate)


# ==============================================================================
# Multi-Step Evaluation Test
# ==============================================================================

TEST = Test(
    name="kotlin_code",
    description="Kotlin code comprehension, refactoring, and feature expansion on Android Camera app",
    setup=[],
    steps=[
        Step(
            prompt="""Analyze source.kt and create a concise summary of what this Android Camera app does in a file called "summary.md". Keep it to 1-2 paragraphs in English.""",
            checks=[
                git_changes("summary.md", "A", total_lines=(1, 25)),
                lang_detect("summary.md", lang="en"),
            ],
            point=2,
        ),
        Step(
            prompt="""In source.kt, add a helper function formatShutterSpeed(exposureTimeNs: Long): String that converts exposure time in nanoseconds to a camera shutter speed string (for example, "1/30s", "1/1000s", or fractional seconds like "0.5s"). Ensure source.kt maintains valid Kotlin syntax.""",
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
                git_changes("source.kt", "M", diff_lines=(4, 40)),
                check_kotlin_syntax("source.kt"),
                check_pattern_in_kotlin(r"enum\s+class\s+FlashMode\b", "enum class FlashMode"),
                check_pattern_in_kotlin(r"FlashMode\.AUTO", "FlashMode.AUTO reference"),
            ],
            point=2,
        ), 
        Step(
            prompt="""Strip all comments""",
            checks=[
                check_kotlin_syntax("source.kt"),
                check_no_comments_in_kotlin("source.kt"),
            ],
            point=2,
        ),
    ],
)
