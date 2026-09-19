"""
eval.trivia_eval - Independent direct OpenAPI runner for trivia benchmark.

Runs trivia quality benchmarks (quizzes 1, 2, 3) directly against an OpenAI-compatible
chat completions endpoint (e.g. vLLM or llama.cpp) without requiring heavy agent/sandbox
coding harnesses.

Supports:
- Boolean (True/False) questions
- Multiple choice questions
- Context reset every 10 questions with initial prompt re-injection
- Configurable reasoning_effort injection
- Automatic score normalization to 20.0 pts and trace generation for benchmark-data.json
"""

from __future__ import annotations

import argparse
import copy
import csv
import json
import logging
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from eval.common import get_harness_logger, load_json_config, setup_logger
from eval.config import (
    API_BASE_URL,
    MODELS_CONFIG_FILE,
    TESTS_DIR,
)

logger = get_harness_logger("trivia")

TRIVIA_TEST_KEY = "trivia"
DEFAULT_TRIVIA_MAX_POINTS = 20.0
CONTEXT_RESET_INTERVAL = 10

INITIAL_PROMPT = (
    "We are going to do some trivia! "
    "For True/False questions, only answer True or False. "
    "For multiple choice questions, only answer with the correct option letter (e.g. A, B, C, or D). "
    "Do not explain your answer or provide extraneous commentary. "
    "NEVER call any tools or look it up! You know all these facts. Reply 'Ready' to begin."
)


def load_quiz_questions(quiz_files: list[str] | None = None) -> list[dict[str, Any]]:
    """Load and combine questions from harmonized quiz CSV files."""
    benchmark_dir = TESTS_DIR / "benchmark"
    if not quiz_files:
        quiz_files = ["private_quiz1.csv", "private_quiz2.csv", "private_quiz3.csv"]

    questions = []
    for q_file in quiz_files:
        f_path = benchmark_dir / q_file if not os.path.isabs(q_file) else Path(q_file)
        if not f_path.is_file():
            logger.warning("Quiz file not found: %s", f_path)
            continue

        with open(f_path, "r", encoding="utf-8", errors="ignore", newline="") as f:
            reader = csv.DictReader(f)
            file_count = 0
            for row in reader:
                q_text = row.get("question", "").strip()
                ans = row.get("correct_answer", "").strip()
                q_type = row.get("type", "boolean").strip().lower()
                diff = row.get("difficulty", "medium").strip()
                cat = row.get("category", "General").strip()
                inc_raw = row.get("incorrect_answers", "[]")

                try:
                    inc_list = json.loads(inc_raw) if isinstance(inc_raw, str) else list(inc_raw)
                except Exception:
                    inc_list = []

                if q_text and ans:
                    questions.append({
                        "file": f_path.name,
                        "type": q_type,
                        "difficulty": diff,
                        "category": cat,
                        "question": q_text,
                        "correct_answer": ans,
                        "incorrect_answers": inc_list,
                    })
                    file_count += 1
            logger.debug("Loaded %d questions from %s", file_count, f_path.name)

    logger.info("Total trivia questions loaded: %d (from %s)", len(questions), [Path(f).name for f in quiz_files])
    return questions


def validate_trivia_boolean_answer(
    response: str,
    expected_answer: str | bool,
    max_words: int = 50,
) -> tuple[bool, str]:
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


def validate_trivia_multiple_choice_answer(
    response: str,
    expected_letter: str,
    expected_text: str,
    options: dict[str, str],
    max_words: int = 50,
) -> tuple[bool, str]:
    """Validate multiple choice answer matching expected option letter or text without contradictions."""
    if not response or not response.strip():
        return False, "Rejected: empty response"

    cleaned = re.sub(r"<think>[\s\S]*?</think>", "", response, flags=re.IGNORECASE).strip()
    cleaned = re.sub(r"```[\w]*\n?", "", cleaned).replace("```", "").strip()

    if not cleaned:
        return False, "Rejected: empty response"

    words = re.findall(r"\b[\w'-]+\b", cleaned)
    if len(words) > max_words:
        return False, f"Rejected: overly verbose ({len(words)} words > {max_words})"

    expected_letter = expected_letter.strip().upper()
    expected_text_clean = expected_text.strip().lower()
    all_letters = [k.upper() for k in options.keys()]

    # Check for multiple conflicting option letters
    found_letters = set()
    for let in all_letters:
        if re.search(rf"\b{let}(?:\)|\.|\:|\b)", cleaned, re.IGNORECASE):
            found_letters.add(let)

    if len(found_letters) > 1:
        return False, f"Rejected: ambiguous response contains multiple options ({sorted(found_letters)})"

    # 1. Matches first token letter (e.g. "A", "A)", "A. ...")
    first_token_match = re.match(r"^\s*([A-Za-z])(?:\)|\.|\:|\s|$)", cleaned)
    if first_token_match:
        cand = first_token_match.group(1).upper()
        if cand == expected_letter:
            return True, f"Correct ({expected_letter})"
        elif cand in all_letters:
            return False, f"Incorrect: selected {cand}, expected {expected_letter} ({expected_text})"

    # 2. Correct letter mentioned unambiguously
    if found_letters == {expected_letter}:
        return True, f"Correct ({expected_letter})"

    # 3. Text match for full answer string
    if expected_text_clean in cleaned.lower():
        incorrect_texts = [options[k].lower() for k in options if k.upper() != expected_letter and len(options[k]) > 3]
        if not any(it in cleaned.lower() for it in incorrect_texts):
            return True, f"Correct ({expected_text})"

    return False, f"Incorrect: expected {expected_letter} ({expected_text}), got \"{cleaned[:60]}\""


def format_multiple_choice_question(
    question: str,
    correct_answer: str,
    incorrect_answers: list[str],
) -> tuple[str, str, dict[str, str]]:
    """Format question with deterministic letter options A, B, C, D...

    Returns:
        (formatted_prompt, correct_letter, options_dict)
    """
    all_choices = sorted(list(set([correct_answer] + list(incorrect_answers))))
    letters = ["A", "B", "C", "D", "E", "F"][:len(all_choices)]
    options = dict(zip(letters, all_choices))

    correct_letter = "A"
    for l, text in options.items():
        if text == correct_answer:
            correct_letter = l
            break

    opt_lines = [f"{l}) {options[l]}" for l in letters]
    prompt = f"{question}\n" + "\n".join(opt_lines)
    return prompt, correct_letter, options


def send_chat_completion(
    base_url: str,
    model: str,
    messages: list[dict[str, str]],
    reasoning_effort: str | None = None,
    timeout: int = 30,
) -> tuple[str, str, dict[str, int]]:
    """Send chat completion request to OpenAPI endpoint.

    Returns:
        (content, reasoning_content, token_usage_dict)
    """
    target_url = f"{base_url.rstrip('/')}/v1/chat/completions"
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "max_tokens": 1024,
    }
    if reasoning_effort and reasoning_effort.lower() not in ("off", "none", "on", ""):
        payload["reasoning_effort"] = reasoning_effort.lower()

    req = urllib.request.Request(
        target_url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )

    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))

    msg = data.get("choices", [{}])[0].get("message", {})
    content = msg.get("content") or ""
    reasoning = msg.get("reasoning") or msg.get("reasoning_content") or ""

    # If content was empty due to length limit or backend behavior, fallback to reasoning
    if not content and reasoning:
        content = reasoning

    usage = data.get("usage", {})
    comp_details = usage.get("completion_tokens_details") or {}
    reasoning_toks = comp_details.get("reasoning_tokens") if isinstance(comp_details, dict) else 0
    token_usage = {
        "prompt_tokens": usage.get("prompt_tokens", 0),
        "completion_tokens": usage.get("completion_tokens", 0),
        "total_tokens": usage.get("total_tokens", 0),
        "reasoning_tokens": reasoning_toks or 0,
    }
    return content, reasoning, token_usage


def run_trivia_benchmark(
    base_url: str = API_BASE_URL,
    model: str | None = None,
    reasoning_effort: str | None = None,
    quiz_files: list[str] | None = None,
    max_questions: int | None = None,
    max_points: float = DEFAULT_TRIVIA_MAX_POINTS,
    context_reset_interval: int = CONTEXT_RESET_INTERVAL,
    verbose: bool = False,
) -> dict[str, Any]:
    """Execute trivia benchmark against OpenAPI chat completion endpoint directly.

    Args:
        base_url: Base URL of the OpenAI-compatible server (e.g. http://spark:8000).
        model: Target model ID. Resolved via /v1/models if empty.
        reasoning_effort: Thinking effort parameter (e.g. "low", "medium", "xhigh").
        quiz_files: List of quiz CSV filenames to evaluate. Defaults to quizzes 1, 2, 3.
        max_questions: Optional limit on number of questions (for quick validation).
        max_points: Normalized maximum score (default: 10.0 pts).
        context_reset_interval: Number of questions before clearing context (default: 10).
        verbose: Enable verbose logging.

    Returns:
        Dict containing 'summary' and 'trace' matching benchmark-data and full_trace schemas.
    """
    if verbose:
        logger.setLevel(logging.DEBUG)

    # 1. Resolve server model ID from /v1/models for the API payload
    from eval.results import resolve_model_info
    server_model_id, _ = resolve_model_info(base_url, fallback_name=model or "")

    # 2. Resolve reasoning_effort from models.json using benchmark model name
    bench_model_key = model or server_model_id
    if not reasoning_effort and os.path.isfile(MODELS_CONFIG_FILE):
        cfg = load_json_config(MODELS_CONFIG_FILE)
        if bench_model_key in cfg:
            reasoning_effort = cfg[bench_model_key].get("reasoning_effort")
        else:
            for k, v in cfg.items():
                if bench_model_key and (k == bench_model_key or k in bench_model_key or bench_model_key in k):
                    reasoning_effort = v.get("reasoning_effort")
                    break

    logger.info("=" * 70)
    logger.info("RUNNING trivia benchmark (model=%s [server_id=%s], reasoning_effort=%s, reset_interval=%d) against %s",
                bench_model_key, server_model_id, reasoning_effort or "default", context_reset_interval, base_url)
    logger.info("=" * 70)

    # 3. Load questions
    all_questions = load_quiz_questions(quiz_files)
    effective_limit = max_questions
    if effective_limit is None and os.environ.get("TRIVIA_MAX_QUESTIONS"):
        try:
            effective_limit = int(os.environ["TRIVIA_MAX_QUESTIONS"])
        except ValueError:
            pass

    if effective_limit and effective_limit > 0:
        all_questions = all_questions[:effective_limit]
        logger.info("Limited trivia questions to first %d questions", len(all_questions))

    if not all_questions:
        logger.error("No questions loaded for trivia benchmark!")
        return {}

    total_questions = len(all_questions)
    point_per_q = round(max_points / total_questions, 4) if total_questions > 0 else 0.0

    step_traces = []
    earned_raw_points = 0
    passed_steps = 0
    total_tokens_in = 0
    total_tokens_out = 0
    peak_context_tokens = 0

    t_bench_start = time.perf_counter()

    # Session message history
    messages: list[dict[str, str]] = []

    for idx, q in enumerate(all_questions):
        # Context Reset Check: Every context_reset_interval questions, clear and re-inject initial prompt
        if idx % context_reset_interval == 0:
            logger.info("--- [Context Reset: Session batch %d - Q%d..Q%d] ---",
                        (idx // context_reset_interval) + 1, idx + 1, min(idx + context_reset_interval, total_questions))
            messages = [{"role": "user", "content": INITIAL_PROMPT}]
            try:
                ready_content, ready_reasoning, ready_usage = send_chat_completion(
                    base_url=base_url,
                    model=server_model_id,
                    messages=messages,
                    reasoning_effort=reasoning_effort,
                )
                messages.append({"role": "assistant", "content": ready_content})
                total_tokens_in += ready_usage.get("prompt_tokens", 0)
                total_tokens_out += ready_usage.get("completion_tokens", 0)
                peak_context_tokens = max(peak_context_tokens, ready_usage.get("prompt_tokens", 0))
            except Exception as e:
                logger.warning("Error initializing trivia session turn: %s", e)

        # Build question prompt
        q_type = q["type"]
        if q_type == "multiple":
            q_prompt, correct_letter, opt_dict = format_multiple_choice_question(
                q["question"], q["correct_answer"], q["incorrect_answers"]
            )
        else:
            q_prompt = f"True or False: {q['question']}"
            correct_letter = ""
            opt_dict = {}

        messages.append({"role": "user", "content": q_prompt})

        step_t0 = time.perf_counter()
        step_start_iso = datetime.now(timezone.utc).isoformat()

        try:
            resp_content, resp_reasoning, usage = send_chat_completion(
                base_url=base_url,
                model=server_model_id,
                messages=messages,
                reasoning_effort=reasoning_effort,
            )
            step_tokens_in = usage.get("prompt_tokens", 0)
            step_tokens_out = usage.get("completion_tokens", 0)
            total_tokens_in += step_tokens_in
            total_tokens_out += step_tokens_out
            peak_context_tokens = max(peak_context_tokens, step_tokens_in)
            messages.append({"role": "assistant", "content": resp_content})
        except Exception as e:
            logger.error("Request failed at Q%d: %s", idx + 1, e)
            resp_content = ""
            resp_reasoning = ""
            step_tokens_in = 0
            step_tokens_out = 0

        step_dur = round(time.perf_counter() - step_t0, 2)
        step_end_iso = datetime.now(timezone.utc).isoformat()

        # Validate answer
        if q_type == "multiple":
            is_passed, check_msg = validate_trivia_multiple_choice_answer(
                resp_content, correct_letter, q["correct_answer"], opt_dict
            )
        else:
            is_passed, check_msg = validate_trivia_boolean_answer(
                resp_content, q["correct_answer"]
            )

        step_score = point_per_q if is_passed else 0.0
        if is_passed:
            earned_raw_points += 1
            passed_steps += 1

        status_symbol = "✓" if is_passed else "✗"
        logger.info("----------------------------------------------------------------------")
        logger.info("[%s] [Step %d/%d] Q%d (%s, %s, %.2fs)", status_symbol, idx + 1, total_questions, idx + 1, q["category"], q_type, step_dur)
        logger.info("  Question:   %s", q["question"])
        if q_type == "multiple" and opt_dict:
            opts_str = " | ".join(f"{k}) {v}" for k, v in opt_dict.items())
            logger.info("  Options:    %s", opts_str)

        reasoning_tokens = usage.get("reasoning_tokens", 0)
        reasoning_words = len(resp_reasoning.split()) if resp_reasoning else 0
        if reasoning_tokens > 0 and reasoning_words > 0:
            reasoning_summary = f"{reasoning_tokens} tokens ({reasoning_words} words)"
        elif reasoning_tokens > 0:
            reasoning_summary = f"{reasoning_tokens} tokens"
        elif reasoning_words > 0:
            reasoning_summary = f"{reasoning_words} words"
        else:
            reasoning_summary = "0 tokens"
        logger.info("  Reasoning:  %s", reasoning_summary)

        disp_resp = " ".join(resp_content.strip().split()) if resp_content else "<empty>"
        if len(disp_resp) > 300:
            disp_resp = disp_resp[:300] + f"... [total {len(disp_resp)} chars]"
        logger.info("  Response:   %s", disp_resp)
        logger.info("  Validation: %s %s", status_symbol, check_msg)

        step_traces.append({
            "step_index": idx,
            "step_name": f"Q{idx + 1} ({q['category']})",
            "prompt": q_prompt,
            "point": point_per_q,
            "earned_score": step_score,
            "max_score": point_per_q,
            "start_time": step_start_iso,
            "end_time": step_end_iso,
            "tokens_in": step_tokens_in,
            "tokens_out": step_tokens_out,
            "peak_context_tokens": step_tokens_in,
            "context_used_pct": 0.0,
            "used_hint": False,
            "events": [],
            "evaluation": {
                "passed": is_passed,
                "score": step_score,
                "check_results": [{
                    "passed": is_passed,
                    "message": check_msg,
                    "details": resp_content.strip()[:200],
                }],
            },
            "duration_seconds": step_dur,
        })

    wall_time_sec = round(time.perf_counter() - t_bench_start, 2)

    # Normalize earned score to max_points (10.0)
    completion_rate = round((passed_steps / total_questions) * 100.0, 1) if total_questions > 0 else 0.0
    normalized_earned_score = round((passed_steps / total_questions) * max_points, 2) if total_questions > 0 else 0.0
    normalized_max_score = round(max_points, 2)

    logger.info("=" * 70)
    logger.info(
        "✓ trivia complete: %d/%d passed (%.1f%%) -> Score: %.2f/%.1f pts in %.2fs",
        passed_steps, total_questions, completion_rate, normalized_earned_score, normalized_max_score, wall_time_sec
    )
    logger.info("=" * 70)

    summary_record = {
        "name": TRIVIA_TEST_KEY,
        "earned_score": normalized_earned_score,
        "max_score": normalized_max_score,
        "run_time_sec": wall_time_sec,
        "tokens_in": total_tokens_in,
        "tokens_out": total_tokens_out,
        "peak_context_tokens": peak_context_tokens,
        "context_used_pct": 0.0,
    }

    trace_record = {
        "name": TRIVIA_TEST_KEY,
        "completion_rate": completion_rate,
        "earned_score": normalized_earned_score,
        "max_score": normalized_max_score,
        "passed_steps": passed_steps,
        "total_steps": total_questions,
        "duration_seconds": wall_time_sec,
        "tokens_in": total_tokens_in,
        "tokens_out": total_tokens_out,
        "peak_context_tokens": peak_context_tokens,
        "context_used_pct": 0.0,
        "steps": step_traces,
    }

    return {
        "summary": summary_record,
        "trace": trace_record,
        "passed_steps": passed_steps,
        "total_steps": total_questions,
        "earned_score": normalized_earned_score,
        "max_score": normalized_max_score,
        "wall_time_sec": wall_time_sec,
    }


def main():
    parser = argparse.ArgumentParser(description="Run independent direct OpenAPI trivia benchmark")
    parser.add_argument("--base-url", default=API_BASE_URL, help="Base URL of OpenAI-compatible server")
    parser.add_argument("--model", default=None, help="Model ID")
    parser.add_argument("--reasoning", default=None, help="Reasoning effort (low, medium, xhigh)")
    parser.add_argument("--limit", type=int, default=None, help="Limit number of questions to test")
    parser.add_argument("--quiz", default=None, help="Comma-separated list of quiz numbers (e.g. 1,2 or all)")
    parser.add_argument("--reset-interval", type=int, default=CONTEXT_RESET_INTERVAL, help="Context reset interval")
    parser.add_argument("--v", action="store_true", help="Verbose logging")
    args = parser.parse_args()

    quiz_files = None
    if args.quiz and args.quiz != "all":
        nums = [x.strip() for x in args.quiz.split(",") if x.strip()]
        quiz_files = [f"private_quiz{n}.csv" for n in nums]

    res = run_trivia_benchmark(
        base_url=args.base_url,
        model=args.model,
        reasoning_effort=args.reasoning,
        quiz_files=quiz_files,
        max_questions=args.limit,
        context_reset_interval=args.reset_interval,
        verbose=args.v,
    )
    if res:
        print("\nSUMMARY RECORD:")
        print(json.dumps(res["summary"], indent=2))


if __name__ == "__main__":
    main()
