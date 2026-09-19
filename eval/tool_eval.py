"""
eval.tool_eval - Runner and parser for tool-eval-bench quality benchmark.

Runs tool-eval-bench as an extra non-harness test once per model, normalizes
raw earned points (e.g. 156/176) to 40pts, records wall-clock run time, and
formats results for benchmark-data.json and trace visualization.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from eval.common import get_harness_logger, setup_logger
from eval.config import (
    API_BASE_URL,
    DEFAULT_TOOL_EVAL_MAX_POINTS,
    DEFAULT_TOOL_EVAL_PARALLEL,
    DEFAULT_TOOL_EVAL_TIMEOUT_SECONDS,
    TOOL_EVAL_BENCH_BIN,
)

logger = get_harness_logger("tool-eval")

TOOL_EVAL_TEST_KEY = "tool-eval-bench"


def parse_tool_eval_output(
    data: dict[str, Any] | str,
    wall_time_sec: float,
    max_normalized_points: float = DEFAULT_TOOL_EVAL_MAX_POINTS,
) -> dict[str, Any]:
    """Parse tool-eval-bench JSON result and normalize scores to max_normalized_points (default 40pts).

    Args:
        data: Parsed JSON dict or JSON string from tool-eval-bench.
        wall_time_sec: Total wall-clock execution time in seconds.
        max_normalized_points: Normalized maximum score (default: 40.0).

    Returns:
        Dictionary containing:
          - 'summary': dict matching benchmark-data schema for test_results['tool-eval-bench']
          - 'trace': dict matching suite_trace['tests']['tool-eval-bench']
          - 'raw_scores': raw scores dict from tool-eval-bench
          - 'earned_points': raw points earned (e.g. 156)
          - 'max_benchmark_points': raw benchmark total points (e.g. 176)
          - 'normalized_earned_score': score scaled to max_normalized_points
    """
    if isinstance(data, str):
        # Extract json payload if mixed with external logs
        trimmed = data.strip()
        if not trimmed.startswith("{"):
            match = re.search(r"(\{.*\})", trimmed, re.DOTALL)
            if match:
                trimmed = match.group(1)
        data = json.loads(trimmed)

    scores = data.get("scores", {})
    scenario_results = scores.get("scenario_results", [])
    total_scenarios = len(scenario_results)
    max_benchmark_points = total_scenarios * 2 if total_scenarios > 0 else 176

    # Calculate earned points across ALL scenarios (ignoring score property to account for timed out tests)
    if scenario_results:
        earned_points = sum(r.get("points", 0) for r in scenario_results)
    else:
        earned_points = scores.get("total_points", 0)

    # Normalize to 40 points and round final score to int
    normalized_earned_score = (
        int(round((earned_points / max_benchmark_points) * max_normalized_points))
        if max_benchmark_points > 0
        else 0
    )
    normalized_max_score = int(round(max_normalized_points))

    tokens_in = sum(r.get("prompt_tokens", 0) for r in scenario_results)
    tokens_out = sum(r.get("completion_tokens", 0) for r in scenario_results)
    if tokens_in == 0 and tokens_out == 0 and "total_tokens" in scores:
        tokens_in = scores.get("total_tokens", 0)

    passed_steps = sum(
        1 for r in scenario_results if r.get("status") in ("pass", "PASS") or r.get("points", 0) == 2
    )
    total_steps = total_scenarios if total_scenarios > 0 else 88

    # Build scenario step traces for interactive timeline visualization in trace.html
    step_traces = []
    for idx, r in enumerate(scenario_results):
        sc_id = r.get("scenario_id", f"TC-{idx + 1:02d}")
        sc_points = r.get("points", 0)
        sc_status = str(r.get("status", "")).lower()
        is_passed = (sc_status == "pass") or (sc_points > 0)
        sc_dur = r.get("duration_seconds", 0.0)
        sc_prompt = r.get("expected_behavior") or r.get("summary") or f"Scenario {sc_id}"
        sc_events = []

        # Populate synthetic tool events if present
        for tc in r.get("tool_calls_made", []):
            sc_events.append({
                "type": "tool",
                "tool": tc,
                "input": "",
                "output": "",
            })

        step_traces.append({
            "step_index": idx,
            "step_name": f"{sc_id}: {r.get('summary', '') or sc_prompt}",
            "prompt": sc_prompt,
            "point": 2,
            "earned_score": sc_points,
            "max_score": 2,
            "start_time": "",
            "end_time": "",
            "tokens_in": r.get("prompt_tokens", 0),
            "tokens_out": r.get("completion_tokens", 0),
            "peak_context_tokens": 0,
            "context_used_pct": 0.0,
            "used_hint": False,
            "events": sc_events,
            "evaluation": {
                "passed": is_passed,
                "score": sc_points,
                "check_results": [{
                    "passed": is_passed,
                    "message": r.get("summary") or r.get("note") or f"Status: {sc_status} ({sc_points}/2 pts)",
                    "details": r.get("note") or "",
                }],
            },
            "duration_seconds": sc_dur,
        })

    completion_rate = (
        round((earned_points / max_benchmark_points) * 100.0, 1)
        if max_benchmark_points > 0
        else 0.0
    )

    summary_record = {
        "name": TOOL_EVAL_TEST_KEY,
        "earned_score": normalized_earned_score,
        "max_score": normalized_max_score,
        "run_time_sec": wall_time_sec,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "peak_context_tokens": 0,
        "context_used_pct": 0.0,
    }

    trace_record = {
        "name": "tool-eval-bench",
        "completion_rate": completion_rate,
        "earned_score": normalized_earned_score,
        "max_score": normalized_max_score,
        "passed_steps": passed_steps,
        "total_steps": total_steps,
        "duration_seconds": wall_time_sec,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "peak_context_tokens": 0,
        "context_used_pct": 0.0,
        "steps": step_traces,
    }

    return {
        "summary": summary_record,
        "trace": trace_record,
        "raw_scores": scores,
        "earned_points": earned_points,
        "max_benchmark_points": max_benchmark_points,
        "normalized_earned_score": normalized_earned_score,
        "wall_time_sec": wall_time_sec,
    }


def _ensure_tool_eval_bench_imported() -> bool:
    """Ensure tool_eval_bench package is importable, adding local uv tool paths if needed."""
    try:
        import tool_eval_bench  # noqa: F401
        return True
    except ImportError:
        import sys
        candidate_site_packages = list(Path.home().glob(".local/share/uv/tools/tool-eval-bench/lib/python*/site-packages"))
        for p in candidate_site_packages:
            if str(p) not in sys.path and p.is_dir():
                sys.path.insert(0, str(p))
        try:
            import tool_eval_bench  # noqa: F401
            return True
        except ImportError:
            return False


def _apply_tool_eval_monkeypatches() -> None:
    """Monkey-patch tool_eval_bench adapters in-memory to never send temperature parameter."""
    try:
        import tool_eval_bench.adapters.openai_compat as oac
        import tool_eval_bench.adapters.requests as req_adapter

        # 1. Monkeypatch OpenAI Compatible Adapter stream and non-stream methods
        if not getattr(oac.OpenAICompatibleAdapter, "_temperature_patched", False):
            orig_non_stream = oac.OpenAICompatibleAdapter._non_stream_request
            orig_stream = oac.OpenAICompatibleAdapter._stream_request

            async def patched_non_stream(self, client, url, payload, headers, timeout):
                if isinstance(payload, dict):
                    payload.pop("temperature", None)
                return await orig_non_stream(self, client, url, payload, headers, timeout)

            async def patched_stream(self, client, url, payload, headers, timeout):
                if isinstance(payload, dict):
                    payload.pop("temperature", None)
                return await orig_stream(self, client, url, payload, headers, timeout)

            oac.OpenAICompatibleAdapter._non_stream_request = patched_non_stream
            oac.OpenAICompatibleAdapter._stream_request = patched_stream
            oac.OpenAICompatibleAdapter._temperature_patched = True

        # 2. Monkeypatch minimal_request used for warmup / preflight
        if not getattr(req_adapter, "_temperature_patched", False):
            orig_min_req = req_adapter.minimal_request

            def patched_minimal_request(*args, **kwargs):
                url, payload, headers = orig_min_req(*args, **kwargs)
                if isinstance(payload, dict):
                    payload.pop("temperature", None)
                return url, payload, headers

            req_adapter.minimal_request = patched_minimal_request
            req_adapter._temperature_patched = True

    except Exception as e:
        logger.warning("Could not apply in-memory tool_eval_bench monkeypatches: %s", e)


def find_tool_eval_bench_bin() -> str:
    """Find the executable path for tool-eval-bench fallback."""
    resolved = shutil.which(TOOL_EVAL_BENCH_BIN)
    if resolved:
        return resolved

    candidate_paths = [
        Path.home() / ".local" / "bin" / TOOL_EVAL_BENCH_BIN,
        Path.home() / ".local" / "share" / "uv" / "tools" / "tool-eval-bench" / "bin" / TOOL_EVAL_BENCH_BIN,
    ]
    for p in candidate_paths:
        if p.is_file() and os.access(p, os.X_OK):
            return str(p)

    return TOOL_EVAL_BENCH_BIN


def run_tool_eval_benchmark(
    base_url: str = API_BASE_URL,
    timeout: int = DEFAULT_TOOL_EVAL_TIMEOUT_SECONDS,
    parallel: int = DEFAULT_TOOL_EVAL_PARALLEL,
    hardmode: bool = True,
    reasoning_effort: str | None = None,
    verbose: bool = False,
) -> dict[str, Any] | None:
    """Execute tool-eval-bench using Python bindings with in-memory temperature monkeypatches.

    Falls back to CLI execution if library cannot be imported.
    """
    normalized_effort = str(reasoning_effort).lower().strip() if reasoning_effort else ""

    if _ensure_tool_eval_bench_imported():
        _apply_tool_eval_monkeypatches()
        import asyncio
        import tool_eval_bench.api as tool_eval_api
        from tool_eval_bench.evals.scenarios import ALL_SCENARIOS, ALL_SCENARIOS_WITH_HARDMODE

        scenarios = list(ALL_SCENARIOS_WITH_HARDMODE) if hardmode else list(ALL_SCENARIOS)
        extra_params: dict[str, Any] = {}
        if normalized_effort in ("low", "medium", "xhigh"):
            extra_params["reasoning_effort"] = normalized_effort

        logger.info("=" * 70)
        logger.info("RUNNING tool-eval-bench via Python API (scenarios=%d, timeout=%ds, parallel=%d, reasoning_effort=%s) against %s",
                    len(scenarios), timeout, parallel, normalized_effort or "default", base_url)
        logger.info("=" * 70)

        t0 = time.perf_counter()
        try:
            raw_result = asyncio.run(
                tool_eval_api.run_benchmark(
                    model="",
                    base_url=base_url,
                    backend="vllm",
                    scenarios=scenarios,
                    timeout_seconds=timeout,
                    concurrency=parallel,
                    extra_params=extra_params if extra_params else None,
                    persist=False,
                )
            )
            wall_time_sec = round(time.perf_counter() - t0, 2)
            parsed = parse_tool_eval_output(raw_result, wall_time_sec=wall_time_sec)
            logger.info(
                "✓ tool-eval-bench finished in %.1fs: Points=%d/%d -> Normalized Score=%.2f/40",
                wall_time_sec,
                parsed["earned_points"],
                parsed["max_benchmark_points"],
                parsed["normalized_earned_score"],
            )
            return parsed
        except Exception as e:
            logger.error("Failed to execute tool-eval-bench Python binding: %s", e)

    # Fallback to CLI execution if Python API unavailable
    bin_path = find_tool_eval_bench_bin()
    cmd = [
        bin_path,
        "bench",
        "--base-url",
        base_url,
        "--timeout",
        str(timeout),
        "--parallel",
        str(parallel),
        "--json",
    ]
    if hardmode:
        cmd.append("--hardmode")
    if normalized_effort in ("low", "medium", "xhigh"):
        cmd.extend(["--backend-kwargs", json.dumps({"reasoning_effort": normalized_effort})])

    logger.info("=" * 70)
    logger.info("RUNNING tool-eval-bench CLI fallback: %s", " ".join(cmd))
    logger.info("=" * 70)

    t0 = time.perf_counter()
    try:
        with tempfile.TemporaryDirectory(prefix="tool_eval_bench_") as tmp_dir:
            cmd_with_output = list(cmd)
            cmd_with_output.extend(["--output-dir", tmp_dir])
            proc = subprocess.run(
                cmd_with_output,
                cwd=tmp_dir,
                capture_output=True,
                text=True,
            )
    except Exception as e:
        logger.error("Failed to execute tool-eval-bench: %s", e)
        return None

    wall_time_sec = round(time.perf_counter() - t0, 2)

    if proc.returncode != 0:
        logger.warning("tool-eval-bench exited with status code %d", proc.returncode)
        if proc.stderr:
            logger.warning("tool-eval-bench stderr:\n%s", proc.stderr[:500])

    if not proc.stdout or not proc.stdout.strip():
        logger.error("tool-eval-bench produced no stdout JSON output.")
        return None

    try:
        parsed = parse_tool_eval_output(proc.stdout, wall_time_sec=wall_time_sec)
        logger.info(
            "✓ tool-eval-bench finished in %.1fs: Points=%d/%d -> Normalized Score=%.2f/40",
            wall_time_sec,
            parsed["earned_points"],
            parsed["max_benchmark_points"],
            parsed["normalized_earned_score"],
        )
        return parsed
    except Exception as e:
        logger.error("Failed to parse tool-eval-bench JSON: %s\nOutput was:\n%s", e, proc.stdout[:500])
        return None
