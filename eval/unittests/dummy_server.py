"""
eval.unittests.dummy_server - Lightweight in-process mock OpenAI/vLLM HTTP server.

Used by test suites to exercise the full evaluation pipeline without needing
a GPU cluster or real LLM backend.
"""

import http.server
import json
import socketserver
import threading
import time
from typing import Any

__all__ = [
    "DummyServerHandler",
    "start_dummy_server",
    "stop_dummy_server",
]


class DummyServerHandler(http.server.BaseHTTPRequestHandler):
    """Handles mock OpenAI/vLLM HTTP endpoints."""

    def log_message(self, _format: str, *args: Any) -> None:
        # Suppress noisy standard HTTP access logs during test runs
        pass

    def _send_json(self, status_code: int, data: Any) -> None:
        payload = json.dumps(data).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:
        path = self.path.split("?")[0].rstrip("/")
        if path in ("", "/health", "/v1/health"):
            self._send_json(200, {"status": "ok"})
        elif path in ("/models", "/v1/models"):
            self._send_json(200, {
                "object": "list",
                "data": [
                    {
                        "id": "dummy-model",
                        "object": "model",
                        "created": int(time.time()),
                        "owned_by": "vllm",
                        "max_model_len": 8192,
                    }
                ],
            })
        else:
            self._send_json(404, {"error": f"Path '{self.path}' not found"})

    def do_POST(self) -> None:
        path = self.path.split("?")[0].rstrip("/")
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length).decode("utf-8") if content_length > 0 else "{}"
        try:
            req_data = json.loads(body)
        except Exception:
            req_data = {}

        if path in ("/chat/completions", "/v1/chat/completions"):
            is_stream = req_data.get("stream", False)
            model = req_data.get("model", "dummy-model")

            if is_stream:
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "keep-alive")
                self.end_headers()

                # Stream token chunks
                chunks = [
                    {"id": "chatcmpl-test", "object": "chat.completion.chunk", "choices": [{"index": 0, "delta": {"role": "assistant", "content": "I am a "}}]},
                    {"id": "chatcmpl-test", "object": "chat.completion.chunk", "choices": [{"index": 0, "delta": {"content": "dummy response."}}]},
                    {"id": "chatcmpl-test", "object": "chat.completion.chunk", "choices": [{"index": 0, "delta": {}}], "usage": {"prompt_tokens": 12, "completion_tokens": 6, "total_tokens": 18}},
                ]
                for c in chunks:
                    self.wfile.write(f"data: {json.dumps(c)}\n\n".encode("utf-8"))
                    self.wfile.flush()
                self.wfile.write(b"data: [DONE]\n\n")
                self.wfile.flush()
            else:
                self._send_json(200, {
                    "id": "chatcmpl-test",
                    "object": "chat.completion",
                    "created": int(time.time()),
                    "model": model,
                    "choices": [
                        {
                            "index": 0,
                            "message": {
                                "role": "assistant",
                                "content": "I am a dummy response.",
                            },
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 12,
                        "completion_tokens": 6,
                        "total_tokens": 18,
                    },
                })
        else:
            self._send_json(404, {"error": f"Path '{self.path}' not found"})


class ThreadedHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def start_dummy_server(host: str = "0.0.0.0", port: int = 0) -> tuple[http.server.HTTPServer, str]:
    """Start the dummy OpenAI/vLLM HTTP server in a background thread.

    Returns:
        (server_instance, base_url_string) e.g. (server, "http://127.0.0.1:54321")
    """
    server = ThreadedHTTPServer((host, port), DummyServerHandler)
    assigned_port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{assigned_port}"
    return server, base_url


def stop_dummy_server(server: http.server.HTTPServer) -> None:
    """Shutdown and close the dummy server."""
    try:
        server.shutdown()
        server.server_close()
    except Exception:
        pass
