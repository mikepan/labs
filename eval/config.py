"""
eval.config - Centralized configurations, path definitions, and environment defaults.
"""

from pathlib import Path

# Repository Root Directory (Path object)
REPO_ROOT = Path(__file__).resolve().parent.parent

# Cluster & Remote Host Configuration
REMOTE_HOST = "mike@spark"
REMOTE_VLLM_DIR = "~/apps/spark-vllm-docker"
API_BASE_URL = "http://spark:8000"

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

# Agent & Model Generation Limits
DEFAULT_MAX_OUTPUT_TOKENS = 65536       # Max output tokens limit
DEFAULT_CONTEXT_WINDOW = 262144         # Fallback max context window limit

# Tool-Eval-Bench Quality Benchmark Defaults
DEFAULT_TOOL_EVAL_TIMEOUT_SECONDS = 600   # Request timeout per scenario in seconds
DEFAULT_TOOL_EVAL_PARALLEL = 2           # Number of parallel workers
DEFAULT_TOOL_EVAL_MAX_POINTS = 40.0      # Normalized maximum score points
TOOL_EVAL_BENCH_BIN = "tool-eval-bench"

# Data & Config Paths (Path objects)
MODELS_CONFIG_FILE = REPO_ROOT / "eval" / "models.json"
HARNESSES_CONFIG_FILE = REPO_ROOT / "eval" / "harnesses.json"
BENCHMARK_DATA_FILE = REPO_ROOT / "site" / "results" / "benchmark-data.json"
BENCHMARK_SCHEMA_FILE = REPO_ROOT / "eval" / "benchmark-data.schema.json"
BENCHMARK_RESULT_FILE = REPO_ROOT / "result.txt"
RESULTS_DIR = REPO_ROOT / "site" / "results"
TESTS_DIR = REPO_ROOT / "tests"

