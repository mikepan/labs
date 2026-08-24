import os
import re
import subprocess
import sys

from tests.framework import Step, Test, git_changes, lang_detect, files_identical, custom_check


# ==============================================================================
# Helpers & Validators
# ==============================================================================

def check_bilingual_merge():
    """Verify sky_bilingual.md contains the substantive bilingual content from both
    sky.md (English) and sky2.md (Chinese), and that the originals were removed."""

    def _validate(workspace_dir: str) -> tuple[bool, str]:
        merged_path = os.path.join(workspace_dir, "sky_bilingual.md")
        if not os.path.exists(merged_path):
            return False, "'sky_bilingual.md' was not created."

        with open(merged_path, "r", encoding="utf-8", errors="ignore") as f:
            merged_content = f.read().strip()

        if len(merged_content) < 50:
            return False, "sky_bilingual.md is too short or empty."

        # Verify both English and Chinese scripts are present
        has_cjk = bool(re.search(r"[\u4e00-\u9fff]", merged_content))
        has_latin = bool(re.search(r"[a-zA-Z]{3,}", merged_content))
        if not (has_cjk and has_latin):
            return False, "sky_bilingual.md must contain both English and Chinese text."

        # Retrieve previous versions from git history if available
        def get_git_file(fname: str) -> str | None:
            for ref in ["HEAD", "HEAD~1", "HEAD~2", "HEAD~3"]:
                res = subprocess.run(
                    ["git", "show", f"{ref}:{fname}"],
                    cwd=workspace_dir,
                    capture_output=True,
                    text=True,
                )
                if res.returncode == 0 and res.stdout.strip():
                    return res.stdout
            return None

        sky_en = get_git_file("sky.md")
        sky_zh = get_git_file("sky2.md")

        def clean_md(text: str) -> str:
            text = re.sub(r"^#+\s*", "", text, flags=re.MULTILINE)
            return re.sub(r"[*_`#]", "", text)

        # If previous files are in git history, verify content retention
        if sky_en:
            en_sentences = [s.strip() for s in re.split(r"[.\n]+", clean_md(sky_en)) if len(s.strip()) > 20]
            en_matched = sum(1 for s in en_sentences if s.lower() in merged_content.lower() or s[:20].lower() in merged_content.lower())
            en_ratio = (en_matched / len(en_sentences)) if en_sentences else 1.0
            if en_ratio < 0.70:
                return False, f"English content retention in sky_bilingual.md is too low ({en_ratio:.1%})."

        if sky_zh:
            zh_sentences = [s.strip() for s in re.split(r"[。\n]+", clean_md(sky_zh)) if len(s.strip()) > 10]
            zh_matched = sum(1 for s in zh_sentences if s in merged_content or s[:10] in merged_content)
            zh_ratio = (zh_matched / len(zh_sentences)) if zh_sentences else 1.0
            if zh_ratio < 0.70:
                return False, f"Chinese content retention in sky_bilingual.md is too low ({zh_ratio:.1%})."

        return True, "sky_bilingual.md correctly merged English and Chinese content."

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
                git_changes("index.htm", "A", total_lines=(50, 1000)),
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
