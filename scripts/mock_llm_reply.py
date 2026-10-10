"""阶段 4 E2E:最小 OpenAI 兼容 mock——任何请求都回一句静态助手文本。

供「交给 AI 修复」E2E 使用:修复子代理(agent_jobs → turn_runner)拿它当
provider;子代理零工具调用即完成(status=ok),从而走通
spawn → subagent_result → 自动复核 的全链路,又不依赖任何真实模型。

支持非流式(JSON)与流式(SSE)两种响应;打印启动行供 startMockServer 探测。
"""

from __future__ import annotations

import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

_REPLY = "(mock) 修复子代理已完成本轮处理。"


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_POST(self) -> None:  # noqa: N802 (http.server 命名)
        length = int(self.headers.get("Content-Length", "0") or "0")
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw.decode("utf-8", errors="replace"))
        except json.JSONDecodeError:
            body = {}
        if isinstance(body, dict) and body.get("stream"):
            self._send_stream()
        else:
            self._send_json()

    def _send_json(self) -> None:
        payload = {
            "id": "mock-llm",
            "object": "chat.completion",
            "created": 0,
            "model": "mock",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": _REPLY},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_stream(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()

        def chunk(delta: dict, finish: str | None) -> bytes:
            body = {
                "id": "mock-llm",
                "object": "chat.completion.chunk",
                "created": 0,
                "model": "mock",
                "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
            }
            return f"data: {json.dumps(body, ensure_ascii=False)}\n\n".encode("utf-8")

        self.wfile.write(chunk({"role": "assistant", "content": _REPLY}, None))
        self.wfile.write(chunk({}, "stop"))
        self.wfile.write(b"data: [DONE]\n\n")

    def log_message(self, format: str, *args) -> None:  # noqa: A002
        pass


def main() -> None:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 3465
    server = ThreadingHTTPServer(("127.0.0.1", port), _Handler)
    print(f"mock llm ready http://127.0.0.1:{port}/v1", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
