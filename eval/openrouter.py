"""
eval.openrouter - Host-side OpenRouter gateway with per-run cost accounting.

The evaluated agent must never see the OpenRouter API key. This gateway runs on
the host, is reachable from the sandbox over the LAN, injects the real
credentials, pins the upstream provider, and translates the repo's
vLLM-style reasoning fields into OpenRouter's unified ``reasoning`` object.

It also totals the ``usage.cost`` returned by OpenRouter so each evaluation can
record exactly what the run cost at API pricing.
"""

from __future__ import annotations

import copy
import http.client
import json
import os
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlparse

from eval.common import setup_logger
from eval.config import (
    OPENROUTER_API_BASE,
    OPENROUTER_API_KEY_ENV,
    OPENROUTER_APP_REFERER,
    OPENROUTER_APP_TITLE,
    OPENROUTER_REQUEST_TIMEOUT_SECONDS,
)

__all__ = ["OpenRouterProxy", "normalize_chat_body"]

logger = setup_logger("openrouter")

_UPSTREAM = urlparse(OPENROUTER_API_BASE)


def _lan_ip() -> str:
    """Return the host LAN address the sandbox can use to reach this gateway."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("1.1.1.1", 80))
        ip = sock.getsockname()[0]
    finally:
        sock.close()
    if ip.startswith("127."):
        raise RuntimeError("Could not determine a LAN IP for the OpenRouter gateway")
    return ip


def _normalize_effort(value: Any) -> str | None:
    """Map repo reasoning sentinels to an OpenRouter effort, or None to omit."""
    if not value:
        return None
    effort = str(value).strip().lower()
    if effort == "on":
        return None
    return "none" if effort == "off" else effort


def normalize_chat_body(body: dict[str, Any], provider_routing: dict[str, Any] | None = None) -> dict[str, Any]:
    """Translate vLLM-specific request fields into OpenRouter's wire format."""
    chat_template_kwargs = body.pop("chat_template_kwargs", None) or {}
    effort = _normalize_effort(body.pop("reasoning_effort", None))
    if effort is None:
        effort = _normalize_effort(
            chat_template_kwargs.get("reasoning_effort") or chat_template_kwargs.get("reasoning_strength")
        )
    if effort is None and chat_template_kwargs.get("enable_thinking") is False:
        effort = "none"
    if effort and "reasoning" not in body:
        body["reasoning"] = {"effort": effort}
    if provider_routing:
        body["provider"] = copy.deepcopy(provider_routing)
    return body


class OpenRouterProxy:
    """Threaded HTTP gateway that forwards chat completions to OpenRouter."""

    def __init__(
        self,
        model_id: str,
        context_window: int,
        provider_routing: dict[str, Any] | None = None,
    ):
        key = os.environ.get(OPENROUTER_API_KEY_ENV)
        if not key:
            raise RuntimeError(f"{OPENROUTER_API_KEY_ENV} is not set; export it before running OpenRouter models")
        self.api_key = key
        self.model_id = model_id
        self.context_window = context_window
        self.provider_routing = provider_routing

        self.cost_usd = 0.0
        self.requests = 0
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.providers: set[str] = set()
        self._lock = threading.Lock()

        self.lan_ip = _lan_ip()
        self._server = ThreadingHTTPServer(("0.0.0.0", 0), _OpenRouterHandler)
        self._server.proxy = self
        self._thread: threading.Thread | None = None
        self.port = self._server.server_address[1]
        self.local_url = f"http://127.0.0.1:{self.port}"
        self.sandbox_url = f"http://{self.lan_ip}:{self.port}"

    def start(self) -> "OpenRouterProxy":
        self._thread = threading.Thread(target=self._server.serve_forever, name="openrouter-gateway", daemon=True)
        self._thread.start()
        logger.info(
            "OpenRouter gateway ready for %s (sandbox: %s, provider routing: %s)",
            self.model_id, self.sandbox_url, self.provider_routing or "default",
        )
        return self

    def stop(self) -> None:
        if self._thread is None:
            return
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)
        self._thread = None

    def record_request(self) -> None:
        with self._lock:
            self.requests += 1

    def record_usage(self, usage: dict[str, Any] | None) -> None:
        if not isinstance(usage, dict):
            return
        with self._lock:
            if usage.get("cost") is not None:
                self.cost_usd += float(usage["cost"])
            self.prompt_tokens += int(usage.get("prompt_tokens") or 0)
            self.completion_tokens += int(usage.get("completion_tokens") or 0)

    def record_provider(self, provider: str | None) -> None:
        if not provider:
            return
        with self._lock:
            self.providers.add(provider)

    def summary(self) -> dict[str, Any]:
        with self._lock:
            return {
                "cost_usd": round(self.cost_usd, 6),
                "requests": self.requests,
                "prompt_tokens": self.prompt_tokens,
                "completion_tokens": self.completion_tokens,
                "providers": sorted(self.providers),
            }


class _OpenRouterHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    @property
    def proxy(self) -> OpenRouterProxy:
        return self.server.proxy

    def log_message(self, format: str, *args: Any) -> None:
        pass

    def _send_json(self, obj: dict[str, Any], status: int = 200) -> None:
        body = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)
        self.close_connection = True

    def do_GET(self) -> None:
        if self.path.rstrip("/") == "/health":
            self._send_json({"status": "ok"})
            return
        if self.path.rstrip("/") == "/v1/models":
            self._send_json({
                "object": "list",
                "data": [{
                    "id": self.proxy.model_id,
                    "object": "model",
                    "owned_by": "openrouter",
                    "max_model_len": self.proxy.context_window,
                }],
            })
            return
        self._send_json({"error": "not found"}, status=404)

    def do_POST(self) -> None:
        if not self.path.rstrip("/").endswith("/chat/completions"):
            self._send_json({"error": "not found"}, status=404)
            return

        length = int(self.headers.get("Content-Length", 0))
        raw_body = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw_body)
        except json.JSONDecodeError:
            self._send_json({"error": "invalid JSON body"}, status=400)
            return

        body = normalize_chat_body(body, self.proxy.provider_routing)
        body["model"] = self.proxy.model_id
        payload = json.dumps(body).encode("utf-8")
        self.proxy.record_request()

        headers = {
            "Authorization": f"Bearer {self.proxy.api_key}",
            "Content-Type": "application/json",
            "Accept": self.headers.get("Accept", "application/json"),
            "Accept-Encoding": "identity",
            "HTTP-Referer": OPENROUTER_APP_REFERER,
            "X-Title": OPENROUTER_APP_TITLE,
        }

        conn = http.client.HTTPSConnection(
            _UPSTREAM.hostname,
            _UPSTREAM.port or 443,
            timeout=OPENROUTER_REQUEST_TIMEOUT_SECONDS,
        )
        usage: dict[str, Any] | None = None
        headers_sent = False
        try:
            conn.request("POST", f"{_UPSTREAM.path.rstrip('/')}{self.path}", body=payload, headers=headers)
            resp = conn.getresponse()

            self.send_response(resp.status)
            headers_sent = True
            for key, value in resp.getheaders():
                if key.lower() not in ("transfer-encoding", "content-length", "connection"):
                    self.send_header(key, value)
            self.send_header("Connection", "close")
            self.end_headers()

            content_type = resp.getheader("Content-Type", "")
            if "text/event-stream" in content_type:
                usage = self._relay_stream(resp)
            else:
                data = resp.read()
                usage, provider = self._extract_usage(data)
                self.proxy.record_provider(provider)
                self.wfile.write(data)
                self.wfile.flush()
        except Exception as exc:
            logger.error("OpenRouter gateway request failed: %s", exc)
            if not headers_sent:
                self._send_json({"error": "OpenRouter gateway request failed"}, status=502)
        finally:
            conn.close()
            self.proxy.record_usage(usage)
            self.close_connection = True

    def _relay_stream(self, resp: http.client.HTTPResponse) -> dict[str, Any] | None:
        """Relay SSE chunks immediately while capturing the terminal usage object."""
        buffer = b""
        usage: dict[str, Any] | None = None
        while True:
            chunk = resp.read(65536)
            if not chunk:
                break
            self.wfile.write(chunk)
            self.wfile.flush()
            buffer += chunk
            while b"\n" in buffer:
                line, buffer = buffer.split(b"\n", 1)
                usage = self._parse_sse_line(line) or usage
        if buffer:
            usage = self._parse_sse_line(buffer) or usage
        return usage

    def _parse_sse_line(self, line: bytes) -> dict[str, Any] | None:
        text = line.decode("utf-8", errors="replace").strip()
        if not text.startswith("data:"):
            return None
        data = text[5:].strip()
        if not data or data == "[DONE]":
            return None
        try:
            event = json.loads(data)
        except json.JSONDecodeError:
            return None
        self.proxy.record_provider(event.get("provider"))
        usage = event.get("usage")
        return usage if isinstance(usage, dict) else None

    @staticmethod
    def _extract_usage(data: bytes) -> tuple[dict[str, Any] | None, str | None]:
        try:
            payload = json.loads(data)
        except json.JSONDecodeError:
            return None, None
        usage = payload.get("usage")
        return (usage if isinstance(usage, dict) else None), payload.get("provider")
