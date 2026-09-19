#!/usr/bin/env python3
"""
launch_benchmark.py - High-throughput LLM serving benchmark across configured models.

Usage:
    python3 eval/launch_benchmark.py [--model model_name] [--runs 2] [--v]
"""

import argparse
import csv
import json
import random
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

from eval.common import setup_logger, load_json_config
from eval.config import (
    API_BASE_URL,
    BENCHMARK_RESULT_FILE,
    MODELS_CONFIG_FILE,
    REMOTE_HOST,
    TESTS_DIR,
)
from eval.launch_model import ensure_model_running, stop_model
from eval.results import calculate_model_memory_gb, resolve_model_info

logger = setup_logger("launch_benchmark")
DEFAULT_PROMPT_FILE = TESTS_DIR / "benchmark" / "long-prompt.kt"
NEWS_CSV_FILE = TESTS_DIR / "benchmark" / "news.csv"

# Approximate words-per-token ratio for token estimation
_WORDS_PER_TOKEN = 0.75


def load_prompt(prompt_file: Path | str = DEFAULT_PROMPT_FILE) -> str:
    """Load benchmark prompt text from file."""
    path = Path(prompt_file)
    return f"Describe what this file does (show code snippet when appropriate):\n\n```kotlin\n{path.read_text(encoding='utf-8', errors='ignore')}\n```"


def build_context_pressure_text(target_tokens: int, csv_file: Path = NEWS_CSV_FILE) -> str:
    """Build filler text from news articles to approximately fill target_tokens.

    Uses a simple word-count approximation (1 token ~= 0.75 words).
    Articles are cycled as needed to reach the target.
    """
    target_words = int(target_tokens * _WORDS_PER_TOKEN)

    articles: list[str] = []
    with open(csv_file, encoding="utf-8", errors="ignore", newline="") as f:
        reader = csv.reader(f)
        for row in reader:
            # CSV columns: title, text, subject, date
            if len(row) >= 2 and row[1].strip():
                articles.append(f"{row[0].strip()}\n\n{row[1].strip()}")

    if not articles:
        raise ValueError(f"No articles found in {csv_file}")

    chunks: list[str] = []
    total_words = 0
    pool = articles[:]
    random.shuffle(pool)
    pool_idx = 0
    while total_words < target_words:
        if pool_idx >= len(pool):
            random.shuffle(pool)
            pool_idx = 0
        article = pool[pool_idx]
        chunks.append(article)
        total_words += len(article.split())
        pool_idx += 1

    filler = "\n\n---\n\n".join(chunks)
    logger.info(
        "Context pressure: %d articles, ~%d words, target %d tokens (~%d words)",
        len(chunks), total_words, target_tokens, target_words,
    )
    return filler


def wait_until_idle(base_url: str = API_BASE_URL, timeout_sec: int = 30) -> None:
    """Pause until server has 0 active running requests."""
    start = time.perf_counter()
    while time.perf_counter() - start < timeout_sec:
        try:
            req = urllib.request.Request(f"{base_url}/metrics")
            with urllib.request.urlopen(req, timeout=2.0) as res:
                text = res.read().decode("utf-8", errors="ignore")
                for line in text.splitlines():
                    if line.startswith(("vllm:num_requests_running", "vllm_num_requests_running", "sglang:num_running_reqs")):
                        if float(line.split()[-1]) == 0:
                            return
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return
        except Exception:
            pass
        time.sleep(1.5)


def run_stream_benchmark(
    model: str,
    prompt: str,
    base_url: str = API_BASE_URL,
    verbose: bool = False,
) -> dict[str, Any] | None:
    """Send streaming request to server and calculate throughput metrics."""
    req = urllib.request.Request(
        f"{base_url}/v1/chat/completions",
        data=json.dumps({
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": True,
            "stream_options": {"include_usage": True},
        }).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )

    t0 = time.perf_counter()
    t_first = None
    prompt_tokens = completion_tokens = 0
    chunks: list[str] = []

    try:
        with urllib.request.urlopen(req, timeout=600) as resp:
            for raw_line in resp:
                line = raw_line.decode("utf-8").strip()
                if not line.startswith("data:"):
                    continue
                data_str = line[5:].strip()
                if data_str == "[DONE]":
                    break
                try:
                    chunk = json.loads(data_str)
                except Exception:
                    continue

                for choice in chunk.get("choices", []):
                    delta = choice.get("delta", {})
                    token = delta.get("content") or delta.get("reasoning_content") or delta.get("reasoning")
                    if token:
                        if t_first is None:
                            t_first = time.perf_counter()
                        if verbose:
                            sys.stdout.write(token)
                            sys.stdout.flush()
                        chunks.append(token)

                if chunk.get("usage"):
                    prompt_tokens = chunk["usage"].get("prompt_tokens", prompt_tokens)
                    completion_tokens = chunk["usage"].get("completion_tokens", completion_tokens)

        t_end = time.perf_counter()
        if verbose:
            sys.stdout.write("\n\n")
    except Exception as e:
        logger.error("Benchmark stream request failed: %s", e)
        return None

    ttft = (t_first - t0) if t_first else (t_end - t0)
    decode_time = (t_end - t_first) if t_first else (t_end - t0)
    total_time = t_end - t0
    completion_tokens = completion_tokens or len(chunks)

    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "ttft": ttft,
        "decode_time": decode_time,
        "total_elapsed": total_time,
        "prefill_tps": prompt_tokens / ttft if ttft > 0 else 0,
        "decode_tps": completion_tokens / decode_time if decode_time > 0 else 0,
        "eff_gen_tps": completion_tokens / total_time if total_time > 0 else 0,
    }


def format_table(headers: list[str], rows: list[list[str]], title: str = "") -> str:
    """Render a clean ASCII table."""
    widths = [len(h) for h in headers]
    for row in rows:
        if row != ["---"]:
            for i, cell in enumerate(row):
                widths[i] = max(widths[i], len(str(cell)))

    def format_row(cols):
        return " | ".join(str(c).ljust(widths[i]) for i, c in enumerate(cols))

    sep = "-" * (sum(widths) + 3 * (len(headers) - 1))
    dsep = "=" * len(sep)

    lines = []
    if title:
        lines.extend([dsep, title])
    lines.extend([dsep, format_row(headers), sep])
    for row in rows:
        if row == ["---"]:
            lines.append(sep)
        else:
            lines.append(format_row(row))
    lines.append(dsep)
    return "\n".join(lines)


def benchmark_model(
    model_name: str,
    cfg: dict[str, Any] | None,
    prompt: str,
    runs: int = 1,
    base_url: str = API_BASE_URL,
    manage: bool = True,
    verbose: bool = False,
) -> dict[str, Any] | None:
    """Run benchmark lifecycle for a single model."""
    logger.info("=" * 70)
    logger.info("BENCHMARKING: %s (%d runs)", model_name, runs)
    logger.info("=" * 70)

    try:
        if manage and cfg:
            ok, weight = ensure_model_running(model_name, cfg, host=REMOTE_HOST, base_url=base_url, verbose=True)
            if not ok:
                return None
        else:
            weight, _ = resolve_model_info(f"{base_url}/v1", fallback_name=model_name)

        memory_gb = calculate_model_memory_gb(host=REMOTE_HOST)
        session_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        bench_prompt = f"[Session: {session_id}]\n" + prompt
        run_data = []

        if verbose:
            print("\n" + "=" * 70)
            print("PROMPT SENT:")
            print("=" * 70)
            print(bench_prompt)
            print("=" * 70 + "\n")

        # Warmup pass to ensure JIT is fully compiled
        wait_until_idle(base_url=base_url)
        logger.info("--- Running Warmup Pass for '%s' to ensure JIT is fully compiled ---", model_name)
        warmup_prompt = f"[Warmup: {session_id}]\n" + prompt
        warmup_res = run_stream_benchmark(weight, warmup_prompt, base_url=base_url, verbose=False)
        if warmup_res:
            logger.info(
                "✓ Warmup complete: Prefill=%.2f tok/s (TTFT=%.3fs), Decode=%.2f tok/s, Gen=%d tok",
                warmup_res["prefill_tps"], warmup_res["ttft"], warmup_res["decode_tps"], warmup_res["completion_tokens"]
            )
        else:
            logger.warning("Warmup pass failed or produced no output; proceeding to benchmark.")

        wait_until_idle(base_url=base_url)
        logger.info("Cooling down / waiting 60s for thermal headroom before benchmark...")
        time.sleep(60)
        wait_until_idle(base_url=base_url)

        for idx in range(1, runs + 1):
            wait_until_idle(base_url=base_url)
            logger.info("--- Starting Run %d/%d for '%s' ---", idx, runs, model_name)
            res = run_stream_benchmark(weight, bench_prompt, base_url=base_url, verbose=verbose)
            if res:
                run_data.append(res)
                logger.info(
                    "Run %d: Prefill=%.2f tok/s (TTFT=%.3fs), Decode=%.2f tok/s, Gen=%d tok",
                    idx, res["prefill_tps"], res["ttft"], res["decode_tps"], res["completion_tokens"]
                )

        if not run_data:
            return None

        # Build run summary table
        headers = ["Run", "Prompt Tok", "Gen Tok", "TTFT (s)", "Decode (s)", "Total (s)", "Prefill tps", "Decode tps", "Eff Gen tps"]
        rows = [
            [
                str(i + 1), str(r["prompt_tokens"]), str(r["completion_tokens"]),
                f"{r['ttft']:.3f}", f"{r['decode_time']:.3f}", f"{r['total_elapsed']:.3f}",
                f"{r['prefill_tps']:.2f}", f"{r['decode_tps']:.2f}", f"{r['eff_gen_tps']:.2f}",
            ]
            for i, r in enumerate(run_data)
        ]
        avg = {k: sum(r[k] for r in run_data) / len(run_data) for k in run_data[0].keys()}
        rows.append(["---"])
        rows.append([
            "AVG", f"{avg['prompt_tokens']:.1f}", f"{avg['completion_tokens']:.1f}",
            f"{avg['ttft']:.3f}", f"{avg['decode_time']:.3f}", f"{avg['total_elapsed']:.3f}",
            f"{avg['prefill_tps']:.2f}", f"{avg['decode_tps']:.2f}", f"{avg['eff_gen_tps']:.2f}",
        ])

        size_label = f" | Runtime Size: {memory_gb:.1f} GB" if memory_gb > 0 else ""
        print("\n" + format_table(headers, rows, title=f"BENCHMARK SUMMARY: {model_name} ({len(run_data)} RUNS{size_label})") + "\n")

        return {
            "model": model_name,
            "memory_gb": memory_gb,
            "fresh_prefill_tps": run_data[0]["prefill_tps"],
            "cached_prefill_tps": sum(r["prefill_tps"] for r in run_data[1:]) / len(run_data[1:]) if len(run_data) > 1 else run_data[0]["prefill_tps"],
            "decode_tps": avg["decode_tps"],
            "eff_gen_tps": avg["eff_gen_tps"],
            "ttft": avg["ttft"],
            "gen_tokens": avg["completion_tokens"],
        }
    finally:
        if manage:
            stop_model(host=REMOTE_HOST)


def format_leaderboard(summaries: list[dict[str, Any]], title: str = "OVERALL SERVING BENCHMARK LEADERBOARD") -> str:
    """Render overall serving benchmark leaderboard table."""
    headers = ["Model Name", "Runtime Size", "Fresh Prefill", "Cached Prefill", "Decode tps", "Eff Gen tps", "Avg TTFT (s)", "Gen Tok"]
    rows = [
        [
            s["model"],
            f"{s['memory_gb']:.1f} GB" if s["memory_gb"] > 0 else "N/A",
            f"{s['fresh_prefill_tps']:.2f}",
            f"{s['cached_prefill_tps']:.2f}",
            f"{s['decode_tps']:.2f}",
            f"{s['eff_gen_tps']:.2f}",
            f"{s['ttft']:.3f}",
            f"{s['gen_tokens']:.1f}",
        ]
        for s in summaries
    ]
    return format_table(headers, rows, title=title)


def write_results_file(summaries: list[dict[str, Any]], filepath: Path = BENCHMARK_RESULT_FILE, title: str = "OVERALL SERVING BENCHMARK LEADERBOARD") -> None:
    """Append benchmark leaderboard table to results file."""
    if not summaries:
        return
    table = format_leaderboard(summaries, title=title)
    with open(filepath, "a", encoding="utf-8") as f:
        f.write("\n" + table + "\n")
    logger.info("Appended benchmark results: %s", filepath)


def main():
    parser = argparse.ArgumentParser(description="Run serving benchmark across configured LLM models.")
    parser.add_argument("--model", default=None, help="Model name, comma-separated list of models, or 'all' (default: all)")
    parser.add_argument("--runs", type=int, default=3, help="Number of benchmark runs per model (default: 1)")
    parser.add_argument("--base-url", default=API_BASE_URL, help=f"Base URL of the serving server (default: {API_BASE_URL})")
    parser.add_argument("--no-manage", action="store_true", help="Do not stop or start model, benchmark active server directly")
    parser.add_argument("--title", default="OVERALL SERVING BENCHMARK LEADERBOARD", help="Title for the benchmark leaderboard table")
    parser.add_argument("--context-pressure", type=int, default=25000, metavar="TOKENS",
                        help="Stuff the prompt with news articles to fill approximately TOKENS of context before benchmarking (default: 25000)")
    parser.add_argument("--verbose", action="store_true", help="Print full prompt sent and stream response tokens to stdout")
    args = parser.parse_args()

    prompt = load_prompt()
    if args.context_pressure:
        logger.info("Building context pressure filler: target %d tokens", args.context_pressure)
        filler = build_context_pressure_text(args.context_pressure)
        prompt = f"<context>\n{filler}\n</context>\n\n{prompt}"

    if args.no_manage:
        active_model, _ = resolve_model_info(f"{args.base_url}/v1", fallback_name=args.model or "active-model")
        model_name = args.model if args.model else active_model
        logger.info("Starting direct benchmark on active server at %s for model '%s'", args.base_url, model_name)
        res = benchmark_model(model_name, None, prompt, runs=args.runs, base_url=args.base_url, manage=False, verbose=args.verbose)
        summaries = [res] if res else []
    else:
        models = load_json_config(MODELS_CONFIG_FILE)
        if args.model in ("all", None):
            targets = [m for m, cfg in models.items() if not cfg.get("disabled", False) and cfg.get("enabled", True) is not False]
            disabled_models = [m for m, cfg in models.items() if cfg.get("disabled", False) or cfg.get("enabled", True) is False]
            if disabled_models:
                logger.info("Skipping %d disabled model(s) for 'all': %s", len(disabled_models), disabled_models)
        else:
            targets = [m.strip() for m in args.model.split(",") if m.strip()]

        filtered = []
        for m in targets:
            cfg = models.get(m, {})
            is_variant = False
            for other_m in targets:
                if other_m == m:
                    continue
                other_cfg = models.get(other_m, {})
                m_args = {k: v for k, v in cfg.items() if k != "reasoning_effort"}
                other_args = {k: v for k, v in other_cfg.items() if k != "reasoning_effort"}
                if m_args == other_args:
                    if ("-low" in m or "-medium" in m) and not ("-low" in other_m or "-medium" in other_m):
                        is_variant = True
                        break
            if not is_variant:
                filtered.append(m)
        targets = filtered

        logger.info("Starting benchmark across %d model(s): %s", len(targets), targets)
        summaries = []
        for m in targets:
            res = benchmark_model(m, models[m], prompt, runs=args.runs, base_url=args.base_url, manage=True, verbose=args.verbose)
            if res:
                summaries.append(res)

    if summaries:
        write_results_file(summaries, title=args.title)
        print("\n" + format_leaderboard(summaries, title=args.title) + "\n")


if __name__ == "__main__":
    main()
