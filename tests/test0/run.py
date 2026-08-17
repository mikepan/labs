from tests.framework import ADD, MODIFY, Step, Test, files_identical, git_changes, html_valid, lang_detect

TEST = Test(
    name="test0_multilingual_sky_doc",
    steps=[
        Step(
            prompt="""Why is the sky blue?
Explain it at 3 different levels (3 yearsold, teenager, science PhD) and produce a markdown file called 'sky.md' with a headings for each of those levels.""",
            checks=[
                git_changes("sky.md", ADD, lines=(6, 60)),
                lang_detect("sky.md", lang="en"),
            ],
        ),
        Step(
            prompt="make a copy of this file and rename it to sky2.md",
            checks=[
                git_changes("sky2.md", ADD),
                files_identical("sky.md", "sky2.md"),
            ],
        ),
        Step(
            prompt="translate sky2.md to simplified chinese",
            checks=[
                git_changes("sky2.md", MODIFY, lines=(6, 60)),
                lang_detect("sky2.md", lang="zh"),
            ],
        ),
        Step(
            prompt="""Create a beautiful single page html(index.htm) to present this content, showing the english and chinese content side by side. Be sure that the js/css are all embedded. Dont load any external resources from the web.""",
            checks=[
                git_changes("index.htm", ADD, lines=(100, 10000)),
                html_valid("index.htm", standalone=True),
            ],
        ),
    ],
)
