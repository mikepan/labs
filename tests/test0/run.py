import os, sys

_repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from tests.framework import *


TEST = Test(
    name="test0_multilingual_sky_doc",
    steps=[
        Step(
            prompt="""Why is the sky blue?
Explain it at 3 different levels (3 yearsold, teenager, science PhD) and produce a markdown file called 'sky.md' with a headings for each of those levels.""",
            checks=[
                git_changes("sky.md", "A", lines=(6, 60)),
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
                git_changes("sky2.md", "M", lines=(6, 60)),
                lang_detect("sky2.md", lang="zh"),
            ],
            point=3,
        ),
        Step(
            prompt="""Create a beautiful single page html(index.htm) to present this content, showing the english and chinese content side by side. Be sure that the js/css are all embedded. Dont load any external resources from the web.""",
            checks=[
                git_changes("index.htm", "A", lines=(50, 5000)),
            ],
            point=4,
        ),
    ],
)
