import csv
from pathlib import Path
import re

from tests.framework import Step, Test, custom_check

CSV_PATH = Path(__file__).resolve().parent / "quiz_questions.csv"


def load_quiz_questions() -> list[dict[str, str]]:
    """Load all boolean trivia questions from CSV."""
    questions = []
    with open(CSV_PATH, "r", encoding="utf-8", errors="ignore", newline="") as f:
        for row in csv.DictReader(f):
            q_text = row.get("question", "").strip()
            ans = row.get("correct_answer", "").strip()
            if q_text and ans:
                questions.append({
                    "category": row.get("category", "General").strip(),
                    "question": q_text,
                    "correct_answer": ans,
                })
    return questions


def validate_trivia_answer(response: str, expected_answer: str | bool, max_words: int = 10) -> tuple[bool, str]:
    """Validate that the answer provides the correct True/False polarity without contradictions or verbosity."""
    if not response or not response.strip():
        return False, "Rejected: empty response"

    cleaned = re.sub(r"<think>[\s\S]*?</think>", "", response, flags=re.IGNORECASE).strip()
    cleaned = re.sub(r"```[\w]*\n?", "", cleaned).replace("```", "").strip()

    if not cleaned:
        return False, "Rejected: empty response"

    words = re.findall(r"\b[\w'-]+\b", cleaned)
    if len(words) > max_words:
        return False, f"Rejected: overly verbose ({len(words)} words > {max_words})"

    non_empty_lines = [l for l in cleaned.splitlines() if l.strip()]
    if len(non_empty_lines) > 2:
        return False, f"Rejected: overly verbose ({len(non_empty_lines)} lines)"

    has_pos = bool(re.search(r"\b(true|yes|correct)\b", cleaned, re.IGNORECASE))
    has_neg = bool(re.search(r"\b(false|no|incorrect)\b", cleaned, re.IGNORECASE))

    if has_pos and has_neg:
        return False, "Rejected: answer contains both affirmative and negative indicators"

    if not has_pos and not has_neg:
        return False, "Rejected: no true/false, yes/no, or correct/incorrect found"

    expected_bool = str(expected_answer).strip().lower() in ("true", "1", "yes", "t")
    agent_bool = has_pos

    if agent_bool == expected_bool:
        return True, f"Correct ({'True' if agent_bool else 'False'})"
    return False, f"Incorrect: expected {expected_bool}, got {agent_bool}"


def check_trivia_answer(expected_answer: str):
    """Assertion using the standard custom_check mechanism, exactly like other tests."""
    def _validate(workspace_dir: str, response: str = "") -> tuple[bool, str]:
        return validate_trivia_answer(response, expected_answer)
    return custom_check(_validate)


# Build Test steps using standard checks=[...]
questions = load_quiz_questions()
steps = [
    Step(
        prompt=(
            "We are going to do some trivia. For each question, only answer in True/False. "
            "Do not explain your answer or provide extraneous commentary. Reply 'Ready' to begin."
        ),
        checks=[
            custom_check(lambda ws=None, resp=None: (True, "Trivia session initialized.")),
        ],
        name="Trivia Setup",
        point=0,
    )
]

for idx, q in enumerate(questions):
    steps.append(
        Step(
            prompt=f"True or False: {q['question']}",
            checks=[
                check_trivia_answer(q["correct_answer"]),
            ],
            name=f"Q{idx + 1} ({q['category']})",
            point=1,
        )
    )

TEST = Test(name="trivia", steps=steps)
test = TEST
