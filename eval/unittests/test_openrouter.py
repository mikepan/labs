"""Unit tests for the host-side OpenRouter gateway."""

from __future__ import annotations

import json
import os
import unittest
import urllib.request
from unittest import mock

from eval.openrouter import OpenRouterProxy, normalize_chat_body
from eval.results import get_model_metadata
from eval.run_harness import _sandbox_name


class TestNormalizeChatBody(unittest.TestCase):
    def test_top_level_reasoning_effort_is_translated(self):
        body = normalize_chat_body({"reasoning_effort": "high"})
        self.assertEqual(body["reasoning"], {"effort": "high"})
        self.assertNotIn("reasoning_effort", body)

    def test_chat_template_kwargs_are_translated_and_removed(self):
        body = normalize_chat_body({"chat_template_kwargs": {"reasoning_effort": "low"}})
        self.assertEqual(body["reasoning"], {"effort": "low"})
        self.assertNotIn("chat_template_kwargs", body)

    def test_enable_thinking_false_maps_to_none(self):
        body = normalize_chat_body({"chat_template_kwargs": {"enable_thinking": False}})
        self.assertEqual(body["reasoning"], {"effort": "none"})

    def test_on_omits_reasoning(self):
        body = normalize_chat_body({"reasoning_effort": "on"})
        self.assertNotIn("reasoning", body)

    def test_provider_routing_is_pinned(self):
        routing = {"only": ["alibaba"], "allow_fallbacks": False}
        body = normalize_chat_body({}, routing)
        self.assertEqual(body["provider"], routing)
        self.assertIsNot(body["provider"], routing)


class TestModelMetadata(unittest.TestCase):
    def test_run_config_is_used_without_reloading_models_json(self):
        config = {
            "provider": "openrouter",
            "model-id": "deepseek/deepseek-v4.1-flash-1017",
            "context-window": 1000000,
            "company": "DeepSeek",
            "base-model": "deepseek/deepseek-v4.1-flash-1017",
            "kv-quant": "N/A",
            "llm-server": "OpenRouter",
            "launch-config": "openrouter deepseek/deepseek-v4.1-flash-1017",
            "memory-gb": 0,
        }
        meta = get_model_metadata(
            "DeepSeek-V4.1-Flash-1017-OpenRouter",
            base_url="http://127.0.0.1:1",
            reasoning_effort="high",
            model_config=config,
        )
        self.assertEqual(meta["company"], "DeepSeek")
        self.assertEqual(meta["llm_server"], "OpenRouter")
        self.assertEqual(meta["memory_gb"], 0.0)
        self.assertEqual(meta["kv_quant"], "N/A")


class TestSandboxNaming(unittest.TestCase):
    def test_local_sandbox_name_is_stable(self):
        self.assertEqual(_sandbox_name("pi", is_openrouter=False), "workspace-runner-pi")

    def test_openrouter_sandbox_names_are_unique(self):
        first = _sandbox_name("pi", is_openrouter=True)
        second = _sandbox_name("pi", is_openrouter=True)
        self.assertNotEqual(first, second)
        self.assertTrue(first.startswith("workspace-runner-pi-"))


class TestOpenRouterProxy(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-key"})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.proxy = OpenRouterProxy(
            model_id="deepseek/deepseek-v4.1-flash-1017",
            context_window=1000000,
            provider_routing={"only": ["alibaba"], "allow_fallbacks": False},
        )

    def tearDown(self):
        self.proxy.stop()

    def test_models_endpoint_reports_configured_model(self):
        self.proxy.start()
        with urllib.request.urlopen(f"{self.proxy.local_url}/v1/models", timeout=5) as resp:
            data = json.loads(resp.read())
        self.assertEqual(data["data"][0]["id"], "deepseek/deepseek-v4.1-flash-1017")
        self.assertEqual(data["data"][0]["max_model_len"], 1000000)

    def test_usage_accumulates_cost_and_tokens(self):
        self.proxy.record_usage({"cost": 0.001, "prompt_tokens": 10, "completion_tokens": 4})
        self.proxy.record_usage({"cost": 0.002, "prompt_tokens": 5, "completion_tokens": 2})
        self.assertEqual(
            self.proxy.summary(),
            {"cost_usd": 0.003, "requests": 0, "prompt_tokens": 15, "completion_tokens": 6, "providers": []},
        )

    def test_missing_api_key_fails_fast(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(RuntimeError):
                OpenRouterProxy(model_id="x", context_window=1)


if __name__ == "__main__":
    unittest.main()
