import os
import sys

from tests.framework import Step, Test, git_changes, lang_detect, files_identical


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
            point=1,
        ),
        Step(
            prompt="make a copy of this file and rename it to sky2.md",
            checks=[
                git_changes("sky2.md", "A"),
                files_identical("sky.md", "sky2.md"),
            ],
            point=2,
        ),
        Step(
            prompt="translate sky2.md to simplified chinese and write back to the same file",
            checks=[
                git_changes("sky2.md", "M", total_lines=(6, 100)),
                lang_detect("sky2.md", lang="zh"),
            ],
            point=2,
        ),
        Step(
            prompt="""Create a beautiful single page html(index.htm) to present this content, showing the english and chinese content side by side. Be sure that the js/css are all embedded. Dont load any external resources from the web.""",
            checks=[
                git_changes("index.htm", "A", total_lines=(50, 500)),
            ],
            point=2,
        ),
        Step(
            prompt="""Add a copyright Jennifer Robins 2026 notice at the bottom  of the page""",
            checks=[
                git_changes("index.htm", "A", diff_lines=(1,100)),
            ],
            point=1,
        ),
        Step(
            prompt="""Add a theme toggle button to index.htm that allows switching between 'Day Mode' (light blue) and 'Night Mode' (dark starry sky) with smooth CSS transitions.""",
            checks=[
                git_changes("index.htm", "M", total_lines=(50, 2000), diff_lines=(10, 1500)),
            ],
            point=2,
        ),
        Step(
            prompt="""let's not use any javascript""",
            checks=[
                git_changes("index.htm", "M", total_lines=(50, 2000)),
            ],
            point=2,
        )
    ],
)
