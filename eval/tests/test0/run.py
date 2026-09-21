import os
import re
import subprocess

from eval.tests.framework import Step, Test, git_changes, lang_detect, files_identical, custom_check


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

        clean_merged = clean_md(merged_content)

        # If previous files are in git history, verify content retention
        if sky_en:
            en_sentences = [s.strip() for s in re.split(r"[.\n]+", clean_md(sky_en)) if len(s.strip()) > 20]
            en_matched = sum(1 for s in en_sentences if s.lower() in clean_merged.lower() or s[:20].lower() in clean_merged.lower())
            en_ratio = (en_matched / len(en_sentences)) if en_sentences else 1.0
            if en_ratio < 0.50:
                return False, f"English content retention in sky_bilingual.md is too low ({en_ratio:.1%})."

        if sky_zh:
            zh_sentences = [s.strip() for s in re.split(r"[。\n]+", clean_md(sky_zh)) if len(s.strip()) > 10]
            zh_matched = sum(1 for s in zh_sentences if s in clean_merged or s[:10] in clean_merged)
            zh_ratio = (zh_matched / len(zh_sentences)) if zh_sentences else 1.0
            if zh_ratio < 0.50:
                return False, f"Chinese content retention in sky_bilingual.md is too low ({zh_ratio:.1%})."

        return True, "sky_bilingual.md correctly merged English and Chinese content."

    return custom_check(_validate)


def check_html_metadata_standards():
    """Verify all 3 HTML presentations have standard charset, html lang tag, and author footer."""

    def _validate(workspace_dir: str) -> tuple[bool, str]:
        # Robust regex for footer: handles author id, Jennifer Robins, and 2026
        footer_pattern = re.compile(
            r'<footer\b[^>]*\bid=["\']author["\'][^>]*>.*?Jennifer\s+Robins.*?2026.*?<\/footer>',
            re.IGNORECASE | re.DOTALL,
        )
        # Robust regex for charset: handles <meta charset="utf-8"> and <meta http-equiv=... charset=utf-8>
        charset_pattern = re.compile(
            r'<meta\b[^>]*\bcharset=["\']?utf-8["\']?',
            re.IGNORECASE,
        )
        # Robust regex for <html lang="...">
        html_lang_pattern = re.compile(
            r'<html\b[^>]*\blang=["\']?([a-zA-Z0-9_\-]+)["\']?',
            re.IGNORECASE,
        )

        expected_langs = {
            "index.html": "en",
            "earth.html": "ar",
        }

        for fname, expected_lang in expected_langs.items():
            path = os.path.join(workspace_dir, fname)
            if not os.path.exists(path):
                return False, f"Expected file '{fname}' not found."

            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()

            # Check footer
            if not footer_pattern.search(content):
                return False, f"File '{fname}' is missing or has malformed footer: expected '<footer id=\"author\">Created by Dr. Jennifer Robins - 2026</footer>'."

            # Check meta charset
            if not charset_pattern.search(content):
                return False, f"File '{fname}' is missing valid <meta charset=\"utf-8\">."

            # Check html lang
            lang_match = html_lang_pattern.search(content)
            if not lang_match or not lang_match.group(1).lower().startswith(expected_lang):
                actual_lang = lang_match.group(1) if lang_match else "none"
                return False, f"File '{fname}' has incorrect html lang attribute '{actual_lang}' (expected '{expected_lang}')."

        return True, "All HTML files adhere to required metadata, lang, and footer standards."

    return custom_check(_validate)


def check_rebrand():
    """Verify that all occurrences of 'Jennifer Robins' have been replaced with 'Prof. J. Robins'."""

    def _validate(workspace_dir: str) -> tuple[bool, str]:
        unreplaced_pattern = re.compile(r'\bJennifer\s+Robins\b', re.IGNORECASE)
        rebranded_pattern = re.compile(r'Prof\.\s*J\.\s*Robins', re.IGNORECASE)

        html_files = ["index.html", "earth.html"]
        for root, _, files in os.walk(workspace_dir):
            if ".git" in root:
                continue
            for f in files:
                if f.endswith((".html", ".md")):
                    fpath = os.path.join(root, f)
                    with open(fpath, "r", encoding="utf-8", errors="ignore") as file_obj:
                        content = file_obj.read()
                    if unreplaced_pattern.search(content):
                        return False, f"Found unreplaced 'Jennifer Robins' in file '{f}'."

        for fname in html_files:
            fpath = os.path.join(workspace_dir, fname)
            if os.path.exists(fpath):
                with open(fpath, "r", encoding="utf-8", errors="ignore") as file_obj:
                    content = file_obj.read()
                if not rebranded_pattern.search(content):
                    return False, f"File '{fname}' does not contain the rebranded name 'Prof. J. Robins'."

        return True, "Rebranding verified: 'Jennifer Robins' successfully replaced with 'Prof. J. Robins'."

    return custom_check(_validate)


# ==============================================================================
# Test Definition
# ==============================================================================

TEST = Test(
    name="multilingual_science_presentation",
    steps=[
        Step(
            prompt="""Why is the sky blue? Explain it for 3 different audiences (a 3-year-old toddler, a high school teenager, and a physics PhD) and save it as a markdown file called 'sky.md' with clear headings for each level.""",
            checks=[
                git_changes("sky.md", "A", total_lines=(6, 200)),
                lang_detect("sky.md", lang="en"),
            ],
        ),
        Step(
            prompt="Make a copy of sky.md and name it sky2.md.",
            checks=[
                git_changes("sky2.md", "A"),
                files_identical("sky.md", "sky2.md"),
            ],
        ),
        Step(
            prompt="Translate sky2.md into Simplified Chinese and save the translation back to the same file.",
            checks=[
                git_changes("sky2.md", "M", total_lines=(6, 200)),
                lang_detect("sky2.md", lang="zh"),
            ],
        ),
        Step(
            prompt="""Create a clean, simple, single-page HTML presentation in 'index.html' to display this bilingual content side-by-side (English and Chinese). Keep all CSS and JavaScript embedded directly without loading external resources.""",
            checks=[
                git_changes("index.html", "A", total_lines=(30, 1000)),
            ],
        ),
        Step(
            prompt="""Please add a copyright notice for Jennifer Robins (2026) to the footer of index.html.""",
            checks=[
                git_changes("index.html", "M", diff_lines=(1, 100)),
            ],
        ),
        Step(
            prompt="""Merge sky.md and sky2.md into a single file called 'sky_bilingual.md'. For each explanation level (3-year-old, teenager, PhD), show the English section first followed by the Chinese translation directly below it. Delete sky.md and sky2.md once merged.""",
            checks=[
                git_changes("sky_bilingual.md", "A", total_lines=(10, 400)),
                git_changes("sky.md", "D"),
                git_changes("sky2.md", "D"),
                check_bilingual_merge(),
            ],
        ),
        Step(
            prompt="""Let's create a simple, clean, self-contained visual presentation in Arabic on Earth's geological layers (crust, mantle, core). Name it 'earth.html'. Keep all styling and diagrams embedded with no external dependencies.""",
            checks=[
                git_changes("earth.html", "A", total_lines=(30, 1000)),
            ],
        ),
        Step(
            prompt="""Let's do a QA pass across our HTML pages (index.html and earth.html). Ensure every HTML file includes: (1) an appropriate <html lang="..."> attribute for its language ("en" for index.html, "ar" for earth.html), (2) a <meta charset="utf-8"> tag, and (3) an author footer at the bottom: <footer id="author">Created by Dr. Jennifer Robins - 2026</footer>. Update any HTML files that are missing these elements.""",
            checks=[
                git_changes("index.html", "M"),
                git_changes("earth.html", "M"),
                check_html_metadata_standards(),
            ],
        )
    ],
)
