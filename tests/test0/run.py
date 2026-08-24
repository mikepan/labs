import os
import re
import subprocess
import sys

from tests.framework import Step, Test, git_changes, lang_detect, files_identical, custom_check


# ==============================================================================
# Helpers
# ==============================================================================

def _git_show_previous(workspace_dir: str, filepath: str) -> str | None:
    """Retrieve file content from HEAD (the previous step's commit) via git."""
    res = subprocess.run(
        ["git", "show", f"HEAD:{filepath}"],
        cwd=workspace_dir,
        capture_output=True,
        text=True,
    )
    if res.returncode != 0:
        return None
    return res.stdout


def _extract_text_lines(content: str) -> list[str]:
    """Extract substantive text lines (non-empty, non-heading-only, stripped)."""
    lines = []
    for line in content.splitlines():
        stripped = line.strip()
        # Skip empty lines and pure markdown headings (just '#' chars)
        if not stripped or re.match(r"^#+\s*$", stripped):
            continue
        lines.append(stripped)
    return lines


def check_bilingual_merge():
    """Verify sky_bilingual.md contains all substantive content from both
    the previous sky.md (English) and sky2.md (Chinese), retrieved from git history."""

    def _validate(workspace_dir: str) -> tuple[bool, str]:
        # Read the merged file from the working tree
        merged_path = os.path.join(workspace_dir, "sky_bilingual.md")
        if not os.path.exists(merged_path):
            available = [f for f in os.listdir(workspace_dir) if not f.startswith(".")]
            return False, f"'sky_bilingual.md' not found. Available files: {available}"

        with open(merged_path, "r", encoding="utf-8", errors="ignore") as f:
            merged_content = f.read()

        # Retrieve the previous versions from the last commit (HEAD)
        sky_en = _git_show_previous(workspace_dir, "sky.md")
        sky_zh = _git_show_previous(workspace_dir, "sky2.md")

        if sky_en is None:
            return False, "Could not retrieve previous sky.md from git history (HEAD)."
        if sky_zh is None:
            return False, "Could not retrieve previous sky2.md from git history (HEAD)."

        # Extract substantive text lines and check each appears in the merged file
        en_lines = _extract_text_lines(sky_en)
        zh_lines = _extract_text_lines(sky_zh)

        if not en_lines:
            return False, "Previous sky.md had no substantive text lines to verify."
        if not zh_lines:
            return False, "Previous sky2.md had no substantive text lines to verify."

        missing_en = []
        for line in en_lines:
            if line not in merged_content:
                missing_en.append(line)

        missing_zh = []
        for line in zh_lines:
            if line not in merged_content:
                missing_zh.append(line)

        if missing_en:
            preview = missing_en[0][:80]
            return False, (
                f"{len(missing_en)}/{len(en_lines)} English lines from sky.md are missing in sky_bilingual.md. "
                f"First missing: '{preview}...'"
            )

        if missing_zh:
            preview = missing_zh[0][:80]
            return False, (
                f"{len(missing_zh)}/{len(zh_lines)} Chinese lines from sky2.md are missing in sky_bilingual.md. "
                f"First missing: '{preview}...'"
            )

        # Verify both languages are actually present (not just one)
        has_cjk = bool(re.search(r"[\u4e00-\u9fff]", merged_content))
        has_latin = bool(re.search(r"[a-zA-Z]{3,}", merged_content))
        if not has_cjk:
            return False, "sky_bilingual.md contains no Chinese characters."
        if not has_latin:
            return False, "sky_bilingual.md contains no English text."

        return True, (
            f"All {len(en_lines)} English lines and {len(zh_lines)} Chinese lines "
            f"verified present in sky_bilingual.md."
        )

    return custom_check(_validate)


# ==============================================================================
# Test Definition
# ==============================================================================

TEST = Test(
    name="multilingual_science_presentation",
    description="Multi-step multilingual content creation with translation, HTML presentation, and iterative refinement",
    steps=[
        Step(
            prompt="""Why is the sky blue?
Explain it at 3 different levels (3 yearsold, teenager, science PhD) and produce a markdown file called 'sky.md' with a headings for each of those levels.""",
            checks=[
                git_changes("sky.md", "A", total_lines=(6, 100)),
                lang_detect("sky.md", lang="en"),
            ],
        ),
        Step(
            prompt="make a copy of this file and rename it to sky2.md",
            checks=[
                git_changes("sky2.md", "A"),
                files_identical("sky.md", "sky2.md"),
            ],
        ),
        Step(
            prompt="translate sky2.md to simplified chinese and write back to the same file",
            checks=[
                git_changes("sky2.md", "M", total_lines=(6, 100)),
                lang_detect("sky2.md", lang="zh"),
            ],
        ),
        Step(
            prompt="""Create a beautiful single page html(index.htm) to present this content, showing the english and chinese content side by side. Be sure that the js/css are all embedded. Dont load any external resources from the web.""",
            checks=[
                git_changes("index.htm", "A", total_lines=(50, 500)),
            ],
        ),
        Step(
            prompt="""Add a copyright Jennifer Robins 2026 notice at the bottom  of the page""",
            checks=[
                git_changes("index.htm", "M", diff_lines=(1,100)),
            ],
        ),
        Step(
            prompt="""Add a theme toggle button to index.htm that allows switching between 'Day Mode' (light blue) and 'Night Mode' (dark starry sky) with smooth CSS transitions.""",
            checks=[
                git_changes("index.htm", "M", total_lines=(50, 2000), diff_lines=(10, 1500)),
            ],
        ),
        Step(
            prompt="""let's not use any javascript""",
            checks=[
                git_changes("index.htm", "M", total_lines=(50, 2000)),
            ],
        ),
        Step(
            prompt="""Merge sky.md and sky2.md into a single file called sky_bilingual.md. For each explanation level (3 year old, teenager, PhD), show the English explanation first, then the Chinese translation directly below it. Delete sky.md and sky2.md afterwards.""",
            checks=[
                git_changes("sky_bilingual.md", "A", total_lines=(10, 200)),
                git_changes("sky.md", "D"),
                git_changes("sky2.md", "D"),
                check_bilingual_merge(),
            ],
        ),
    ],
)
