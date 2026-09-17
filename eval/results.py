"""
eval.results - Evaluation results saving, model metadata, and benchmark dataset management.
"""

import fcntl
import json
import os
import re
import shutil
import uuid
from typing import Any

from eval.common import setup_logger, run_cmd, http_json, load_json_config
from eval.config import (
    API_BASE_URL,
    BENCHMARK_DATA_FILE,
    MODELS_CONFIG_FILE,
    REMOTE_HOST,
    RESULTS_DIR,
)

__all__ = [
    "get_vllm_model_info",
    "resolve_model_info",
    "calculate_model_memory_gb",
    "get_model_metadata",
    "save_evaluation_results",
]

logger = setup_logger("results")


def get_vllm_model_info(base_url: str) -> dict[str, Any] | None:
    """Fetch model info from /v1/models in a single call."""
    url = f"{base_url}/models" if not base_url.endswith("/models") else base_url
    data = http_json(url, timeout=3)
    if data and "data" in data and len(data["data"]) > 0:
        return data["data"][0]
    return None


def resolve_model_info(base_url: str, fallback_name: str = "") -> tuple[str, int]:
    """Query /v1/models on LLM server and resolve active model ID and max context length."""
    model_info = get_vllm_model_info(base_url)
    active_model = (model_info.get("id") if model_info else None) or fallback_name
    max_context = int(model_info.get("max_model_len", 262144)) if model_info else 262144
    return active_model, max_context


def calculate_model_memory_gb(host: str = REMOTE_HOST) -> float:
    """Query total model memory footprint (GPU dedicated memory + Host Unified managed memory)."""
    gpu_mem = 0.0
    sys_mem = 0.0
    try:
        cmd = ["ssh", host, "nvidia-smi --query-compute-apps=used_gpu_memory --format=csv,noheader,nounits"] if host else [
            "nvidia-smi", "--query-compute-apps=used_gpu_memory", "--format=csv,noheader,nounits"
        ]
        res = run_cmd(*cmd, timeout=5)
        if res.stdout:
            mems = [float(x) for x in res.stdout.split() if x.replace('.', '', 1).isdigit()]
            if mems:
                gpu_mem = max(mems) / 1024.0
    except Exception as e:
        logger.warning("Could not query GPU memory via nvidia-smi: %s", e)

    try:
        # Check system / unified memory RSS for vLLM EngineCore
        rss_script = 'pgrep -f "VLLM::EngineCore" | head -n1 | xargs -I{} grep -E "VmHWM|VmRSS" /proc/{}/status 2>/dev/null'
        cmd = ["ssh", host, rss_script] if host else ["bash", "-c", rss_script]
        res = run_cmd(*cmd, timeout=5)
        if res.stdout:
            for line in res.stdout.splitlines():
                if "VmHWM:" in line or "VmRSS:" in line:
                    parts = line.split()
                    if len(parts) >= 2 and parts[1].isdigit():
                        sys_mem = max(sys_mem, float(parts[1]) / (1024.0 * 1024.0))
    except Exception as e:
        logger.debug("Could not query process system memory: %s", e)

    total_mem = round(gpu_mem + sys_mem, 1) if sys_mem > 5.0 else round(gpu_mem, 1)
    logger.info("Measured model memory: %.1f GB (GPU: %.1f GB, Host Unified: %.1f GB)", total_mem, gpu_mem, sys_mem)
    return total_mem


def get_model_metadata(model_name: str, base_url: str, memory_gb: float | None = None, reasoning_effort: str | None = None) -> dict[str, Any]:
    """Build model metadata from models.json and live server endpoints."""
    launch_cfg = "vllm serve"
    spec_type = "off"
    kv_type = "FP16"
    effort = reasoning_effort or "off"
    company = "Community"
    base_model = model_name

    if os.path.exists(MODELS_CONFIG_FILE):
        models = load_json_config(MODELS_CONFIG_FILE)
        if model_name in models:
            cfg = models[model_name]
            spec_arg = cfg.get("model-arg-speculative", "")
            kv_arg = cfg.get("model-arg-kv-quant", "")
            launch_cfg = cfg.get("model-arg", "")
            if not reasoning_effort:
                effort = cfg.get("reasoning_effort", "off")

            m_serve = re.search(r"vllm\s+serve\s+([^\s]+)", launch_cfg)
            if m_serve:
                full_path = m_serve.group(1)
                company, _, base_model = full_path.rpartition("/") if "/" in full_path else ("Community", "", full_path)

            if spec_arg:
                m = re.search(r'"method":\s*"([^"]+)"', spec_arg)
                spec_type = m.group(1) if m else "on"

            if kv_arg:
                m = re.search(r"--kv-cache-dtype\s+(\S+)", kv_arg)
                kv_type = m.group(1).upper() if m else kv_arg

    # Single API call for both model ID and context length
    vllm_info = get_vllm_model_info(base_url=base_url)
    context_length = 0
    llm_server = "vLLM"
    if vllm_info:
        meta_dict = vllm_info.get("meta") or {}
        context_length = int(vllm_info.get("max_model_len") or meta_dict.get("n_ctx") or 0)
        if vllm_info.get("owned_by") == "llamacpp" or "ftype" in meta_dict:
            llm_server = "llama.cpp"
            kv_type = meta_dict.get("ftype", kv_type)
            launch_cfg = f"llama-server --model {model_name}"

    if memory_gb is None or memory_gb <= 0:
        memory_gb = calculate_model_memory_gb()

    return {
        "display_name": model_name,
        "company": company,
        "base_model": base_model,
        "kv_quant": kv_type,
        "context_length": context_length,
        "memory_gb": memory_gb,
        "speculative_decoding": spec_type,
        "reasoning": effort,
        "launch_config": launch_cfg,
        "llm_server": llm_server,
    }



def _build_benchmark_fields(
    eval_id: str,
    meta: dict[str, Any],
    benchmark_date: str,
    harness_name: str,
    harness_version: str,
    avg_completion: float,
    task_speed: float,
    intel_density: float,
) -> dict[str, Any]:
    """Build the common field set shared by suite_trace and benchmark record."""
    return {
        "eval_id": eval_id,
        "name": meta["display_name"],
        "company": meta["company"],
        "base_model": meta["base_model"],
        "kv_quant": meta["kv_quant"],
        "context_length": meta["context_length"],
        "memory_gb": meta["memory_gb"],
        "benchmark_date": benchmark_date,
        "llm_server": meta.get("llm_server", "vLLM"),
        "speculative_decoding": meta["speculative_decoding"],
        "harness": harness_name,
        "harness_version": harness_version,
        "reasoning": meta["reasoning"],
        "launch_config": meta["launch_config"],
        "intelligence": avg_completion,
        "task_speed": task_speed,
        "intelligence_density": intel_density,
    }


def save_evaluation_results(
    model_name: str,
    harness_name: str,
    harness_version: str,
    evaluation_output: dict[str, Any],
    base_url: str = API_BASE_URL,
    memory_gb: float | None = None,
    results_dir: str = RESULTS_DIR,
    benchmark_data_file: str | None = BENCHMARK_DATA_FILE,
    reasoning_effort: str | None = None,
) -> str:
    """Save full results, trace, artifacts and optionally update benchmark-data.json."""
    eval_id = evaluation_output["eval_id"]
    eval_dir = os.path.join(results_dir, eval_id)
    artifacts_dir = os.path.join(eval_dir, "artifacts")
    os.makedirs(artifacts_dir, exist_ok=True)

    suite_trace = evaluation_output["suite_trace"]
    test_results_summary = evaluation_output["test_results_summary"]

    # 1. Copy artifacts from staging
    stage_root = evaluation_output.get("stage_dir")
    if stage_root and os.path.exists(stage_root):
        for item in os.listdir(stage_root):
            src = os.path.join(stage_root, item)
            if item.endswith(".log"):
                shutil.copy2(src, os.path.join(eval_dir, item))
            elif os.path.isdir(src):
                shutil.copytree(src, os.path.join(artifacts_dir, item), dirs_exist_ok=True)
            else:
                shutil.copy2(src, os.path.join(artifacts_dir, item))
        shutil.rmtree(stage_root, ignore_errors=True)

    # 2. Calculate aggregate scores & metadata
    meta = get_model_metadata(model_name, base_url=base_url, memory_gb=memory_gb, reasoning_effort=reasoning_effort)
    total_completion = 0.0
    total_time = 0.0
    num_tests = len(test_results_summary)
    for t_data in test_results_summary.values():
        e_score = t_data.get("earned_score", 0.0)
        m_score = t_data.get("max_score", 1.0)
        total_completion += (e_score / m_score * 100.0) if m_score > 0 else 100.0
        total_time += t_data.get("run_time_sec", 1.0)

    avg_completion = round(total_completion / num_tests, 1) if num_tests > 0 else 0.0
    task_speed = round((num_tests / (total_time / 3600.0)), 1) if total_time > 0 else 1.0
    model_mem = meta["memory_gb"]
    intel_density = round(avg_completion / model_mem, 3) if model_mem > 0 else 0.0

    # 3. Build common fields once, use for both trace and benchmark record
    benchmark_date = suite_trace["start_time"].split("T")[0]
    common = _build_benchmark_fields(
        eval_id, meta, benchmark_date, harness_name, harness_version,
        avg_completion, task_speed, intel_density,
    )

    suite_trace.update(common)

    # Save full_trace.json (compact minified)
    trace_path = os.path.join(eval_dir, "full_trace.json")
    with open(trace_path, "w", encoding="utf-8") as f:
        json.dump(suite_trace, f, separators=(",", ":"))

    # 4. Update benchmark-data.json with exclusive file lock & atomic replace
    if benchmark_data_file:
        results_record = {**common, "test_results": test_results_summary}
        try:
            parent_dir = os.path.dirname(benchmark_data_file)
            if parent_dir:
                os.makedirs(parent_dir, exist_ok=True)
            lock_path = f"{benchmark_data_file}.lock"
            with open(lock_path, "w", encoding="utf-8") as lock_file:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
                try:
                    if os.path.exists(benchmark_data_file):
                        with open(benchmark_data_file, "r", encoding="utf-8") as f:
                            bench_data = json.load(f)
                    else:
                        bench_data = {
                            "$schema": "../../eval/benchmark-data.schema.json",
                            "evaluations": [],
                        }

                    bench_data.setdefault("evaluations", []).insert(0, results_record)
                    temp_file = f"{benchmark_data_file}.tmp.{os.getpid()}_{uuid.uuid4().hex[:8]}"
                    with open(temp_file, "w", encoding="utf-8") as f:
                        json.dump(bench_data, f, indent=2)
                        f.write("\n")
                    os.replace(temp_file, benchmark_data_file)
                finally:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
            logger.info("✓ Updated benchmark dataset: %s", benchmark_data_file)
        except Exception as e:
            logger.warning("Could not update %s: %s", benchmark_data_file, e)

    logger.info("EVALUATION RESULTS SAVED to: %s", eval_dir)
    return eval_dir
