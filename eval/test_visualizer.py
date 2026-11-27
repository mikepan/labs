"""
eval.test_visualizer - static charts for the benchmark traces.

Usage:
    python3 -m eval.test_visualizer
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Patch

from eval.config import REPO_ROOT, RESULTS_DIR

__all__ = ["plot_context", "main"]

CODING_TESTS = [
    "chess_movegen",
    "kotlin_code",
    "normal_map_classification",
    "data_analytics",
]
TEST_LABELS = ["Chess movegen", "Kotlin refactor", "Normal map", "Data analytics"]
CONTEXT_CHART = REPO_ROOT / "analytics" / "context.png"

MODEL_COLORS = {
    "Qwen3.8-27B-FP8-low": "#9dc3e6",
    "Qwen3.8-27B-FP8-medium": "#2e75b6",
    "Qwen3.8-27B-FP8-xhigh": "#1f4e79",
    "Qwen3.8-27B-NVFP4-low": "#83c5be",
    "Qwen3.8-27B-NVFP4-medium": "#2a9d8f",
    "Qwen3.8-27B-NVFP4-xhigh": "#006d77",
    "Qwen3.8-Flash-Next-NVFP4-QAD-low": "#b7e4c7",
    "Qwen3.8-Flash-Next-NVFP4-QAD-medium": "#52b788",
    "Qwen3.8-Flash-Next-NVFP4-QAD-xhigh": "#2d6a4f",
    "Qwen3.6-27B-NVFP4": "#f59e0b",
    "Qwen3.6-27B-NVFP4-off": "#b45309",
    "Qwen3.6-35B-A3B-NVFP4": "#c2410c",
    "Qwen3.6-35B-A3B-NVFP4-off": "#7c2d12",
    "Nemotron-3.5-Lightning-30B-A3B-NVFP4": "#8b5cf6",
    "Nemotron-3.5-Lightning-30B-A3B-NVFP4-off": "#5b21b6",
    "Nemotron-3-Super-120B-A12B-NVFP4": "#ec4899",
    "Nemotron-3-Super-120B-A12B-NVFP4-off": "#9d174d",
    "Inkling-2-Mini-low": "#fca5a5",
    "Inkling-2-Mini-medium": "#ef4444",
    "Inkling-2-Mini-high": "#991b1b",
    "Muse-Glimmer-30B-NVFP4-W4A4-low": "#d1d5db",
    "Muse-Glimmer-30B-NVFP4-W4A4-medium": "#6b7280",
    "Muse-Glimmer-30B-NVFP4-W4A4-high": "#374151",
    "diffusiongemma-26B-A4B-IT-NVFP4": "#84cc16",
    "diffusiongemma-26B-A4B-IT-NVFP4-off": "#4d7c0f",
}
UNKNOWN_MODEL_COLOR = "#9ca3af"


def _family(name: str) -> str:
    for prefix in (
        "Qwen3.8-27B-FP8",
        "Qwen3.8-27B-NVFP4",
        "Qwen3.8-Flash-Next",
        "Qwen3.6-27B",
        "Qwen3.6-35B",
        "Nemotron-3.5",
        "Nemotron-3-Super",
        "Inkling",
        "Muse-Glimmer",
        "diffusiongemma",
    ):
        if name.startswith(prefix):
            return prefix
    return name


def _variant_rank(name: str) -> int:
    for rank, suffix in enumerate(("-low", "-medium", "-high", "-xhigh")):
        if name.endswith(suffix):
            return rank
    if name.endswith("-off"):
        return 5
    return 1


def _load_context_peaks() -> dict[str, dict[str, int]]:
    """Peak context per coding test and model from the opencode traces.

    Opencode reports per-call `step-finish` usage, so its peak is a real
    single-request context. Pi's turn-level usage aggregates multiple calls
    and can exceed the model window, so it is not used here.
    """
    peaks: dict[str, dict[str, int]] = defaultdict(dict)
    for path in sorted(RESULTS_DIR.glob("*/full_trace.json")):
        trace = json.loads(path.read_text(encoding="utf-8"))
        if trace.get("llm_server") == "OpenRouter":
            continue
        if not trace["harness"].startswith("opencode"):
            continue
        for test_name in CODING_TESTS:
            test = trace["tests"].get(test_name)
            if test is None:
                continue
            peaks[test_name][trace["name"]] = max(
                step.get("peak_context_tokens") or 0 for step in test["steps"]
            )
    return peaks


def plot_context(output: Path = CONTEXT_CHART) -> Path:
    peaks = _load_context_peaks()
    models = sorted({model for test in CODING_TESTS for model in peaks[test]})
    average = {
        model: sum(peaks[test][model] for test in CODING_TESTS if model in peaks[test])
        / max(1, sum(1 for test in CODING_TESTS if model in peaks[test]))
        for model in models
    }
    models.sort(key=lambda m: (-average[m], _family(m), _variant_rank(m), m))

    plt.rcParams.update(
        {
            "font.family": "DejaVu Sans",
            "font.size": 10,
            "axes.spines.top": False,
            "axes.spines.right": False,
        }
    )
    fig, ax = plt.subplots(figsize=(18.5, 8.2))
    width = 0.8 / len(models)
    positions = range(len(CODING_TESTS))

    for group, test in enumerate(CODING_TESTS):
        for index, model in enumerate(models):
            value = peaks[test].get(model, 0) / 1e3
            ax.bar(
                group - 0.4 + index * width + width / 2,
                value,
                width=width * 0.92,
                color=MODEL_COLORS.get(model, UNKNOWN_MODEL_COLOR),
                edgecolor="white",
                linewidth=0.2,
            )

    for group, test in enumerate(CODING_TESTS):
        model = max(
            (m for m in models if m in peaks[test]),
            key=lambda m: peaks[test][m],
        )
        value = peaks[test][model] / 1e3
        index = models.index(model)
        ax.text(
            group - 0.4 + index * width + width / 2,
            value + 4,
            f"{model}\n{value:.0f} k",
            ha="center",
            va="bottom",
            fontsize=8,
            fontweight="bold",
            bbox=dict(boxstyle="round,pad=0.2", fc="white", ec="none", alpha=0.85),
        )
        ax.bar(
            group - 0.4 + index * width + width / 2,
            value,
            width=width * 0.92,
            color="none",
            edgecolor="black",
            linewidth=1.0,
            zorder=5,
        )

    ax.set_xticks(list(positions))
    ax.set_xticklabels(TEST_LABELS, fontsize=12)
    ax.set_ylabel("Peak context (thousands of tokens)")
    ax.set_xlabel("Coding test")
    ax.set_ylim(0, 280)
    ax.set_title(
        "Peak context length by coding test — one bar per model",
        fontsize=15,
        fontweight="bold",
        loc="left",
        pad=26,
    )
    ax.text(
        0,
        1.006,
        "Each bar is one model’s opencode run, using per-call context, all starting from zero. "
        "Pi’s turn-level usage aggregates multiple calls and is excluded.",
        transform=ax.transAxes,
        fontsize=9,
        color="#555",
    )
    ax.grid(axis="y", alpha=0.22)
    ax.set_axisbelow(True)

    handles = [
        Patch(facecolor=MODEL_COLORS.get(model, UNKNOWN_MODEL_COLOR), label=model)
        for model in models
    ]
    ax.legend(
        handles=handles,
        loc="upper left",
        bbox_to_anchor=(1.01, 1.0),
        frameon=False,
        fontsize=7.2,
        ncol=1,
        title="Model",
        title_fontsize=8.5,
    )

    plt.tight_layout()
    output.parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(output, dpi=200)
    plt.close(fig)
    return output


def main() -> None:
    print(f"wrote {plot_context()}")


if __name__ == "__main__":
    main()
