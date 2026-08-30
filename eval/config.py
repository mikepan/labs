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
DEFAULT_LLM_BASE_URL = f"{API_BASE_URL}/v1"

# Docker Sandbox & Harness Defaults
DEFAULT_BUILDER_SANDBOX_NAME = "workspace-builder"
DEFAULT_WORKER_SANDBOX_NAME = "workspace-runner"
DEFAULT_TEMPLATE_TAG = "eval-base-harness:latest"
DEFAULT_OPENCODE_PORT = 4096
DEFAULT_PI_PORT = 4097

# Timeout Defaults (in minutes)
DEFAULT_MAX_STEP_TIMEOUT_MINUTES = 120  # Safety maximum ceiling per step
DEFAULT_IDLE_TIMEOUT_MINUTES = 10       # Max cold prefill + reasoning baseline at high context

# Data & Config Paths (Path objects)
MODELS_CONFIG_FILE = REPO_ROOT / "eval" / "models.json"
HARNESSES_CONFIG_FILE = REPO_ROOT / "eval" / "harnesses.json"
BENCHMARK_DATA_FILE = REPO_ROOT / "site" / "results" / "benchmark-data.json"
BENCHMARK_SCHEMA_FILE = REPO_ROOT / "eval" / "benchmark-data.schema.json"
BENCHMARK_RESULT_FILE = REPO_ROOT / "result.txt"
RESULTS_DIR = REPO_ROOT / "site" / "results"
TESTS_DIR = REPO_ROOT / "tests"

__all__ = [
    "REPO_ROOT",
    "REMOTE_HOST",
    "REMOTE_VLLM_DIR",
    "API_BASE_URL",
    "DEFAULT_LLM_BASE_URL",
    "DEFAULT_BUILDER_SANDBOX_NAME",
    "DEFAULT_WORKER_SANDBOX_NAME",
    "DEFAULT_TEMPLATE_TAG",
    "DEFAULT_OPENCODE_PORT",
    "DEFAULT_PI_PORT",
    "DEFAULT_MAX_STEP_TIMEOUT_MINUTES",
    "DEFAULT_IDLE_TIMEOUT_MINUTES",
    "MODELS_CONFIG_FILE",
    "HARNESSES_CONFIG_FILE",
    "BENCHMARK_DATA_FILE",
    "BENCHMARK_SCHEMA_FILE",
    "BENCHMARK_RESULT_FILE",
    "RESULTS_DIR",
    "TESTS_DIR",
]
