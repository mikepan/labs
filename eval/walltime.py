"""
eval.walltime - Walltime decomposition for the open-weight benchmark traces.

Splits each trace's elapsed time into decode, prefill, tool execution, step
timeouts, and residual overhead. Token counts come from the traces; throughput
is calibrated from the serving benchmarks in result.txt/results.txt and from
the single-turn SimpleBench timings embedded in each trace.

Usage:
    python3 -m eval.walltime
"""

from __future__ import annotations

import json
import statistics
from collections import Counter, defaultdict
from typing import Any, Iterable

from eval.config import BENCHMARK_RESULT_FILE, REPO_ROOT, RESULTS_DIR

__all__ = ["analyze", "main"]

DIRECT_TESTS = {"trivia", "simplebench", "tool-eval-bench"}
STEP_TIMEOUT_SECONDS = 60 * 60 - 60
RATE_FILES = (BENCHMARK_RESULT_FILE, REPO_ROOT / "eval" / "results.txt")


def _parse_serving_rates(paths: Iterable) -> dict[str, tuple[float, float]]:
    """Return {model: (fresh_prefill_tps, decode_tps)} from the serving tables."""
    rates: dict[str, tuple[float, float]] = {}
    for path in paths:
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            cols = [c.strip() for c in line.split("|")]
            if len(cols) != 8:
                continue
            try:
                fresh, decode = float(cols[2]), float(cols[4])
            except ValueError:
                continue
            rates[cols[0]] = (fresh, decode)
    return rates


def _common_prefix(a: str, b: str) -> int:
    n = 0
    for x, y in zip(a, b):
        if x != y:
            break
        n += 1
    return n


def _rates_for(name: str, rates: dict[str, tuple[float, float]]) -> tuple[float, float]:
    """Pick the serving config with the longest name prefix (handles effort suffixes)."""
    key = max(rates, key=lambda k: (_common_prefix(name, k), len(k)))
    if _common_prefix(name, key) < 8:
        raise KeyError(f"No serving benchmark found for {name!r}")
    return rates[key]


def _load_traces() -> list[dict[str, Any]]:
    traces = []
    for path in sorted(RESULTS_DIR.glob("*/full_trace.json")):
        with open(path, encoding="utf-8") as f:
            trace = json.load(f)
        if trace.get("llm_server") == "OpenRouter":
            continue
        traces.append(trace)
    return traces


def _decode_tps(trace: dict[str, Any]) -> float:
    """Per-stream decode rate, measured from the trace's own SimpleBench steps."""
    bench = trace["tests"]["simplebench"]
    elapsed = sum(s.get("duration_seconds") or 0 for s in bench["steps"])
    output = sum(s.get("tokens_out") or 0 for s in bench["steps"])
    if elapsed <= 0 or output <= 0:
        raise ValueError(f"SimpleBench trace has no usable timing: {trace['name']}")
    return output / elapsed


def _tool_seconds(step: dict[str, Any]) -> float:
    return sum(
        (event.get("data") or {}).get("duration_ms") or 0
        for event in step.get("events", [])
        if event.get("type") == "tool"
    ) / 1000.0


def _timed_out(step: dict[str, Any]) -> bool:
    aborted = ((step.get("evaluation") or {}).get("aborted") or "").upper()
    return "TIMEOUT" in aborted or (step.get("duration_seconds") or 0) >= STEP_TIMEOUT_SECONDS


def _direct_prefill_tokens(test_name: str, test: dict[str, Any]) -> int:
    """Unique prefill tokens for a direct benchmark.

    Trivia and SimpleBench are single-turn, so every input token is unique.
    Tool-Eval scenarios are multi-turn; with N tool rounds the summed prompt
    tokens are about N/2 times the final context, so halve/cap accordingly.
    """
    if test_name != "tool-eval-bench":
        return test["tokens_in"]
    unique = 0.0
    for step in test["steps"]:
        turns = 1 + sum(1 for e in step.get("events", []) if e.get("type") == "tool")
        unique += step["tokens_in"] * min(1.0, 2.0 / turns)
    return int(unique)


def _new_bucket() -> Counter:
    return Counter()


def analyze() -> dict[str, Any]:
    traces = _load_traces()
    rates = _parse_serving_rates(RATE_FILES)
    fallback_tps = statistics.median(_decode_tps(t) for t in traces)

    # Direct benchmarks run several questions at once, so their step durations
    # overlap. Infer the worker count from the traces rather than hard-coding it.
    ratios: dict[str, list[float]] = defaultdict(list)
    for trace in traces:
        for test_name in DIRECT_TESTS:
            test = trace["tests"].get(test_name)
            if test and test.get("duration_seconds"):
                steps = sum(s.get("duration_seconds") or 0 for s in test["steps"])
                ratios[test_name].append(steps / test["duration_seconds"])
    concurrency = {
        name: max(1, round(statistics.median(values)))
        for name, values in ratios.items()
    }

    by_test: dict[str, Counter] = defaultdict(_new_bucket)
    by_model: dict[str, Counter] = defaultdict(_new_bucket)
    by_harness: dict[str, Counter] = defaultdict(_new_bucket)
    seen_direct: set[tuple[str, str]] = set()

    for trace in traces:
        name = trace["name"]
        harness = trace["harness"]
        fresh_tps, _ = _rates_for(name, rates)
        decode_tps = _decode_tps(trace)

        for test_name, test in trace["tests"].items():
            direct = test_name in DIRECT_TESTS
            if direct and (name, test_name) in seen_direct:
                continue
            if direct:
                seen_direct.add((name, test_name))

            wall = test.get("duration_seconds") or 0
            tokens_in = test.get("tokens_in") or 0
            tokens_out = test.get("tokens_out") or 0
            timeout = 0.0
            prefill_tokens = 0
            prefill_seconds = 0.0
            decode_seconds = 0.0
            tool_seconds = 0.0

            if direct:
                prefill_tokens = _direct_prefill_tokens(test_name, test)
                prefill_seconds = prefill_tokens / fresh_tps
                decode_seconds = tokens_out / (concurrency[test_name] * decode_tps)
                tool_seconds = sum(_tool_seconds(s) for s in test["steps"])
            else:
                # The agent reuses one session across steps, so only growth
                # beyond the previous peak needs a fresh prefill.
                previous_peak = 0
                for step in test["steps"]:
                    duration = step.get("duration_seconds") or 0
                    step_tool = _tool_seconds(step)
                    peak = step.get("peak_context_tokens") or 0
                    growth = max(0, peak - previous_peak)
                    step_prefill = growth / fresh_tps
                    step_decode = (step.get("tokens_out") or 0) / decode_tps

                    prefill_tokens += growth
                    prefill_seconds += step_prefill
                    decode_seconds += step_decode
                    tool_seconds += step_tool
                    previous_peak = max(previous_peak, peak)

                    if _timed_out(step):
                        timeout += max(0.0, duration - step_prefill - step_decode - step_tool)

            estimated = prefill_seconds + decode_seconds + tool_seconds + timeout
            available = wall - tool_seconds - timeout
            if estimated > wall > 0 and prefill_seconds + decode_seconds > 0:
                scale = max(0.0, available) / (prefill_seconds + decode_seconds)
                prefill_seconds *= scale
                decode_seconds *= scale

            for bucket in (by_test[test_name], by_model[name], by_harness[harness]):
                bucket["wall"] += wall
                bucket["decode"] += decode_seconds
                bucket["prefill"] += prefill_seconds
                bucket["tool"] += tool_seconds
                bucket["timeout"] += timeout
                bucket["tokens_in"] += tokens_in
                bucket["tokens_out"] += tokens_out
                bucket["prefill_tokens"] += prefill_tokens

    def finalize(bucket: Counter) -> dict[str, float]:
        other = (
            bucket["wall"]
            - bucket["decode"]
            - bucket["prefill"]
            - bucket["tool"]
            - bucket["timeout"]
        )
        return {
            "wall": bucket["wall"],
            "decode": bucket["decode"],
            "prefill": bucket["prefill"],
            "tool": bucket["tool"],
            "timeout": bucket["timeout"],
            "other": other,
            "tokens_in": bucket["tokens_in"],
            "tokens_out": bucket["tokens_out"],
            "prefill_tokens": bucket["prefill_tokens"],
        }

    return {
        "tests": {k: finalize(v) for k, v in by_test.items()},
        "models": {k: finalize(v) for k, v in by_model.items()},
        "harnesses": {k: finalize(v) for k, v in by_harness.items()},
        "traces": len(traces),
        "direct_tests_counted": len(seen_direct),
        "fallback_decode_tps": fallback_tps,
    }


def _hours(seconds: float) -> float:
    return seconds / 3600.0


def _pct(part: float, whole: float) -> str:
    return f"{part / whole * 100:.0f}%" if whole else "—"


def _print_table(title: str, rows: list[dict[str, Any]], total: dict[str, float]) -> None:
    print(f"\n### {title}\n")
    print("| Test | Wall | Decode | Prefill | Tool | Timeout | Other | Tokens in | Tokens out |")
    print("|---|---:|---:|---:|---:|---:|---:|---:|---:|")
    for row in rows:
        print(
            f"| {row['name']} | {_hours(row['wall']):.3f} h ({_pct(row['wall'], total['wall'])}) "
            f"| {_hours(row['decode']):.3f} h ({_pct(row['decode'], row['wall'])}) "
            f"| {_hours(row['prefill']):.3f} h ({_pct(row['prefill'], row['wall'])}) "
            f"| {_hours(row['tool']):.3f} h | {_hours(row['timeout']):.3f} h "
            f"| {_hours(row['other']):.3f} h | {row['tokens_in'] / 1e6:.1f} M "
            f"| {row['tokens_out'] / 1e6:.2f} M |"
        )
    print(
        f"| **Total** | **{_hours(total['wall']):.3f} h** "
        f"| **{_hours(total['decode']):.3f} h ({_pct(total['decode'], total['wall'])})** "
        f"| **{_hours(total['prefill']):.3f} h ({_pct(total['prefill'], total['wall'])})** "
        f"| **{_hours(total['tool']):.3f} h** | **{_hours(total['timeout']):.3f} h** "
        f"| **{_hours(total['other']):.3f} h** | **{total['tokens_in'] / 1e6:.1f} M** "
        f"| **{total['tokens_out'] / 1e6:.2f} M** |"
    )


def main() -> None:
    data = analyze()
    tests = data["tests"]

    total = {key: sum(t[key] for t in tests.values()) for key in next(iter(tests.values()))}
    rows = [{"name": k, **v} for k, v in sorted(tests.items(), key=lambda kv: -kv[1]["wall"])]
    _print_table("Walltime by test", rows, total)

    print(
        f"\nScope: {data['traces']} open-weight traces, "
        f"{data['direct_tests_counted']} direct-benchmark runs (shared across harnesses, counted once)."
    )
    print(
        f"Unique prefill tokens: {total['prefill_tokens'] / 1e6:.1f} M of "
        f"{total['tokens_in'] / 1e6:.1f} M billed input tokens "
        f"({total['prefill_tokens'] / total['tokens_in'] * 100:.1f}% uncached)."
    )
    print(
        f"Effective rates: prefill {total['prefill_tokens'] / total['prefill']:.0f} tok/s, "
        f"decode {total['tokens_out'] / total['decode']:.1f} tok/s."
    )

    models = [{"name": k, **v} for k, v in sorted(data["models"].items(), key=lambda kv: -kv[1]["wall"])]
    _print_table("Walltime by model", models, total)

    print("\n### Walltime by harness\n")
    print("| Harness | Wall | Decode | Prefill | Timeout | Tokens in | Tokens out |")
    print("|---|---:|---:|---:|---:|---:|---:|")
    for name, h in sorted(data["harnesses"].items()):
        print(
            f"| {name} | {_hours(h['wall']):.2f} h | {_hours(h['decode']):.2f} h "
            f"| {_hours(h['prefill']):.2f} h | {_hours(h['timeout']):.2f} h "
            f"| {h['tokens_in'] / 1e6:.1f} M | {h['tokens_out'] / 1e6:.2f} M |"
        )


if __name__ == "__main__":
    main()
