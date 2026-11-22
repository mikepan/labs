"""
eval.simplebench_eval - Independent direct OpenAPI runner for SimpleBench.

Runs the official SimpleBench public set (https://github.com/simple-bench/SimpleBench)
as a single-turn multiple-choice knowledge benchmark against an OpenAI-compatible
chat completions endpoint. Each question is sent in a fresh context with the
repository's own system prompt prepended as a regular user prompt, and the model
must end its response with "Final Answer: X".
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from eval.common import get_harness_logger, load_models, send_chat_completion
from eval.config import API_BASE_URL, TESTS_DIR

logger = get_harness_logger("simplebench")

SIMPLEBENCH_TEST_KEY = "simplebench"
DEFAULT_SIMPLEBENCH_MAX_POINTS = 20.0
DEFAULT_SIMPLEBENCH_TIMEOUT_SECONDS = 1800

SYSTEM_PROMPT_FILE = TESTS_DIR / "benchmark" / "simple_bench_system_prompt.txt"
DATASET_FILE = TESTS_DIR / "benchmark" / "simple_bench_public.json"

# Sampling parameters from the official runner (run_benchmark.py). Local reasoning
# models need a larger output budget than the official 2048 to finish their reasoning.
TEMPERATURE = 0.7
TOP_P = 0.95
MAX_TOKENS = 32768

FINAL_ANSWER_RE = re.compile(r"Final Answer:\s*([A-F])", re.IGNORECASE)


def load_system_prompt(path: Path = SYSTEM_PROMPT_FILE) -> str:
    """Load the system prompt verbatim from the SimpleBench repository."""
    return path.read_text(encoding="utf-8").strip()


def load_questions(dataset_path: Path = DATASET_FILE) -> list[dict[str, Any]]:
    """Load the SimpleBench public set questions."""
    data = json.loads(dataset_path.read_text(encoding="utf-8"))
    return data["eval_data"]


def extract_final_answer(response: str) -> str | None:
    """Extract the option letter following 'Final Answer:' as the official scorer does."""
    match = FINAL_ANSWER_RE.search(response.strip())
    return match.group(1).upper() if match else None


def run_simplebench_benchmark(
    base_url: str = API_BASE_URL,
    model: str | None = None,
    reasoning_effort: str | None = None,
    dataset_path: Path = DATASET_FILE,
    system_prompt_path: Path = SYSTEM_PROMPT_FILE,
    max_questions: int | None = None,
    max_points: float = DEFAULT_SIMPLEBENCH_MAX_POINTS,
    timeout: int = DEFAULT_SIMPLEBENCH_TIMEOUT_SECONDS,
    parallel: int = 1,
    verbose: bool = False,
) -> dict[str, Any]:
    """Execute SimpleBench against an OpenAI-compatible chat completions endpoint.

    Args:
        base_url: Base URL of the OpenAI-compatible server (e.g. http://spark:8000).
        model: Benchmark model name. Resolved via /v1/models if empty.
        reasoning_effort: Thinking effort parameter (e.g. "low", "medium", "xhigh").
        dataset_path: SimpleBench public set JSON.
        system_prompt_path: SimpleBench system prompt file.
        max_questions: Optional limit on number of questions (for quick validation).
        max_points: Normalized maximum score (default: 20.0 pts).
        timeout: Request timeout per question in seconds.
        parallel: Number of concurrent question requests (default: 1).
        verbose: Enable verbose logging.

    Returns:
        Dict containing 'summary' and 'trace' matching benchmark-data and full_trace schemas.
    """
    if verbose:
        logger.setLevel(logging.DEBUG)

    from eval.results import resolve_model_info
    server_model_id, _ = resolve_model_info(base_url, fallback_name=model or "")

    bench_model_key = model or server_model_id
    models_cfg = load_models()
    model_cfg = models_cfg.get(bench_model_key, {})
    if not reasoning_effort:
        reasoning_effort = model_cfg.get("reasoning_effort")
    supports_sampling = not model_cfg.get("no-sampling", False)

    logger.info("=" * 70)
    logger.info("RUNNING simplebench (model=%s [server_id=%s], reasoning_effort=%s, sampling=%s, parallel=%d) against %s",
                bench_model_key, server_model_id, reasoning_effort or "default",
                "on" if supports_sampling else "off", parallel, base_url)
    logger.info("=" * 70)

    system_prompt = load_system_prompt(system_prompt_path)
    questions = load_questions(dataset_path)
    if max_questions and max_questions > 0:
        questions = questions[:max_questions]
        logger.info("Limited simplebench questions to first %d questions", len(questions))

    if not questions:
        logger.error("No questions loaded for simplebench!")
        return {}

    total_questions = len(questions)
    point_per_q = round(max_points / total_questions, 4)
    parallel = max(1, min(parallel, total_questions))

    step_traces = []
    passed_steps = 0
    total_tokens_in = 0
    total_tokens_out = 0
    peak_context_tokens = 0

    t_bench_start = time.perf_counter()

    def fetch_question(idx: int, q: dict[str, Any]) -> dict[str, Any]:
        question_id = q.get("question_id", idx + 1)
        # Fresh single-turn context per question: the system prompt is sent as a
        # regular user prompt, with no history carried over between questions.
        messages = [{
            "role": "user",
            "content": f"{system_prompt}\n\n{q['prompt']}",
        }]

        step_t0 = time.perf_counter()
        step_start_iso = datetime.now(timezone.utc).isoformat()

        request_error = None
        for attempt in range(3):
            try:
                resp_content, _, usage = send_chat_completion(
                    base_url=base_url,
                    model=server_model_id,
                    messages=messages,
                    reasoning_effort=reasoning_effort,
                    max_tokens=MAX_TOKENS,
                    temperature=TEMPERATURE if supports_sampling else None,
                    top_p=TOP_P if supports_sampling else None,
                    timeout=timeout,
                )
                request_error = None
                break
            except Exception as e:
                request_error = e
                logger.warning("Request failed at Q%s (attempt %d/3): %s", question_id, attempt + 1, e)
                time.sleep(2)

        if request_error is not None:
            raise RuntimeError(f"SimpleBench request failed for Q{question_id}: {request_error}") from request_error

        return {
            "content": resp_content,
            "usage": usage,
            "start_time": step_start_iso,
            "end_time": datetime.now(timezone.utc).isoformat(),
            "duration": round(time.perf_counter() - step_t0, 2),
        }

    if parallel > 1:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=parallel) as pool:
            futures = [pool.submit(fetch_question, idx, q) for idx, q in enumerate(questions)]
            fetched = [future.result() for future in futures]
    else:
        fetched = [fetch_question(idx, q) for idx, q in enumerate(questions)]

    for idx, (q, result) in enumerate(zip(questions, fetched)):
        question_id = q.get("question_id", idx + 1)
        expected = str(q["answer"]).strip().upper()
        resp_content = result["content"]
        usage = result["usage"]
        step_start_iso = result["start_time"]
        step_end_iso = result["end_time"]
        step_dur = result["duration"]

        step_tokens_in = usage.get("prompt_tokens", 0)
        step_tokens_out = usage.get("completion_tokens", 0)
        total_tokens_in += step_tokens_in
        total_tokens_out += step_tokens_out
        peak_context_tokens = max(peak_context_tokens, step_tokens_in)

        model_answer = extract_final_answer(resp_content)
        if model_answer is None:
            is_passed, check_msg = False, "Rejected: no 'Final Answer: X' found"
        elif model_answer == expected:
            is_passed, check_msg = True, f"Correct ({expected})"
        else:
            is_passed, check_msg = False, f"Incorrect: expected {expected}, got {model_answer}"

        step_score = point_per_q if is_passed else 0.0
        if is_passed:
            passed_steps += 1

        status_symbol = "✓" if is_passed else "✗"
        logger.info("----------------------------------------------------------------------")
        logger.info("[%s] [Step %d/%d] Q%s (%.2fs)", status_symbol, idx + 1, total_questions, question_id, step_dur)
        logger.info("  Question:   %s", " ".join(q["prompt"].split())[:200])

        reasoning_tokens = usage.get("reasoning_tokens", 0)
        logger.info("  Reasoning:  %s tokens", reasoning_tokens)

        disp_resp = " ".join(resp_content.strip().split()) if resp_content else "<empty>"
        if len(disp_resp) > 300:
            disp_resp = disp_resp[:300] + f"... [total {len(disp_resp)} chars]"
        logger.info("  Response:   %s", disp_resp)
        logger.info("  Validation: %s %s", status_symbol, check_msg)

        step_traces.append({
            "step_index": idx,
            "step_name": f"Q{question_id} (SimpleBench)",
            "prompt": q["prompt"],
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
                    "details": resp_content.strip()[:300],
                }],
            },
            "duration_seconds": step_dur,
        })

    wall_time_sec = round(time.perf_counter() - t_bench_start, 2)

    completion_rate = round((passed_steps / total_questions) * 100.0, 1)
    normalized_earned_score = round((passed_steps / total_questions) * max_points, 2)
    normalized_max_score = round(max_points, 2)

    logger.info("=" * 70)
    logger.info(
        "✓ simplebench complete: %d/%d passed (%.1f%%) -> Score: %.2f/%.1f pts in %.2fs",
        passed_steps, total_questions, completion_rate, normalized_earned_score, normalized_max_score, wall_time_sec
    )
    logger.info("=" * 70)

    summary_record = {
        "name": SIMPLEBENCH_TEST_KEY,
        "earned_score": normalized_earned_score,
        "max_score": normalized_max_score,
        "run_time_sec": wall_time_sec,
        "tokens_in": total_tokens_in,
        "tokens_out": total_tokens_out,
        "peak_context_tokens": peak_context_tokens,
        "context_used_pct": 0.0,
    }

    trace_record = {
        "name": SIMPLEBENCH_TEST_KEY,
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
    parser = argparse.ArgumentParser(description="Run the SimpleBench public set against an OpenAI-compatible server")
    parser.add_argument("--base-url", default=API_BASE_URL, help="Base URL of OpenAI-compatible server")
    parser.add_argument("--model", default=None, help="Model ID")
    parser.add_argument("--reasoning", default=None, help="Reasoning effort (low, medium, xhigh)")
    parser.add_argument("--limit", type=int, default=None, help="Limit number of questions to test")
    parser.add_argument("--timeout", type=int, default=DEFAULT_SIMPLEBENCH_TIMEOUT_SECONDS, help="Request timeout in seconds")
    parser.add_argument("--parallel", type=int, default=1, help="Number of concurrent question requests")
    parser.add_argument("--v", action="store_true", help="Verbose logging")
    args = parser.parse_args()

    res = run_simplebench_benchmark(
        base_url=args.base_url,
        model=args.model,
        reasoning_effort=args.reasoning,
        max_questions=args.limit,
        timeout=args.timeout,
        parallel=args.parallel,
        verbose=args.v,
    )
    if res:
        print("\nSUMMARY RECORD:")
        print(json.dumps(res["summary"], indent=2))


if __name__ == "__main__":
    main()
