"""
eval.config - Centralized configurations, path definitions, and environment defaults.
"""

from pathlib import Path

# Repository Root Directory (Path object)
REPO_ROOT = Path(__file__).resolve().parent.parent

# Cluster & Remote Host Configuration
REMOTE_HOST = "mike@spark"
REMOTE_VLLM_DIR = "~/apps/spark-vllm-docker"
REMOTE_VLLM_IMAGE = "vllm-node"
API_BASE_URL = "http://spark:8000"

# OpenRouter Configuration
OPENROUTER_API_BASE = "https://openrouter.ai/api"
OPENROUTER_API_KEY_ENV = "OPENROUTER_API_KEY"
OPENROUTER_REQUEST_TIMEOUT_SECONDS = 3600
OPENROUTER_APP_REFERER = "https://labs.thepixelary.com"
OPENROUTER_APP_TITLE = "FiniBench"

# Docker Sandbox & Harness Defaults
DEFAULT_BUILDER_SANDBOX_NAME = "workspace-builder"
DEFAULT_WORKER_SANDBOX_NAME = "workspace-runner"
DEFAULT_TEMPLATE_TAG = "eval-base-harness:latest"
DEFAULT_OPENCODE_PORT = 4096
DEFAULT_PI_PORT = 4097

# Timeout Defaults (in minutes)
DEFAULT_MAX_STEP_TIMEOUT_MINUTES = 60     # Safety maximum ceiling per step
DEFAULT_IDLE_TIMEOUT_MINUTES = 5       # Max cold prefill + reasoning baseline at high context
DEFAULT_API_TIMEOUT_SECONDS = 30        # Standard internal API timeout for in-sandbox services

# Evaluation & Scoring Defaults
DEFAULT_STEP_POINT = 1                  # Default points allocated per test step
DEFAULT_HINT_SCORE_FACTOR = 0.5         # Score multiplier when hint is utilized
DEFAULT_NUDGE_REPEAT_THRESHOLD = 10      # Identical tool results before a user-style "you're looping" nudge

# Agent & Model Generation Limits
DEFAULT_MAX_OUTPUT_TOKENS = 65536       # Max output tokens limit
DEFAULT_CONTEXT_WINDOW = 262144         # Fallback max context window limit
DEFAULT_COMPACTION_CONTEXT_FRACTION = 0.5  # Compact when this share of the context is used
DEFAULT_COMPACTION_KEEP_RECENT_TOKENS = 20000  # Pi: recent tokens retained beside the summary


def compaction_reserve_tokens(context_window: int) -> int:
    """Tokens kept free so compaction triggers at the configured fraction of each model's window."""
    return int(context_window * DEFAULT_COMPACTION_CONTEXT_FRACTION)

# Tool-Eval-Bench Quality Benchmark Defaults
DEFAULT_TOOL_EVAL_TIMEOUT_SECONDS = 600   # Request timeout per scenario in seconds
DEFAULT_TOOL_EVAL_MAX_POINTS = 40.0      # Normalized maximum score points

# Direct Benchmark Defaults
DEFAULT_DIRECT_BENCHMARK_PARALLEL = 4    # Concurrent questions; direct tests themselves still run sequentially

# Data & Config Paths (Path objects)
MODELS_CONFIG_FILE = REPO_ROOT / "eval" / "models.json"
HARNESSES_CONFIG_FILE = REPO_ROOT / "eval" / "harnesses.json"
BENCHMARK_DATA_FILE = REPO_ROOT / "site" / "results" / "benchmark-data.json"
BENCHMARK_RESULT_FILE = REPO_ROOT / "eval" / "result.txt"
RESULTS_DIR = REPO_ROOT / "site" / "results"
TESTS_DIR = REPO_ROOT / "eval" / "tests"

