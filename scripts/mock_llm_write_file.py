"""SURE 事件桥接 E2E:静态 mock——第一轮发一条 write_file 工具调用,
工具结果回来后回文本收尾。

用途:在真实应用里把「对话 → 编排器执行工具(词表工具)→ 桥接 →
sure hook ingest」整条观测链路跑通。判定只看请求历史里有没有 role=tool
的消息(与 mock_openai.py 同款),不看内容——两次请求内必然收敛,
零真实模型、零外网。

支持非流式(JSON)与流式(SSE)两种响应;打印启动行供 startMockServer 探测。
"""

from __future__ import annotations

import json
import socketserver
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

_REPLY = "(mock) 探针文件已写入,任务完成。"
_TOOL_ID = "call_bridge_write"
_TOOL_NAME = "write_file"
_TOOL_ARGS = {"path": "sure-bridge-probe.txt", "content": "sure-bridge-probe"}


def _tool_calls() -> list[dict]:
    return [
        {
            "id": _TOOL_ID,
            "type": "function",
            "function": {
                "name": _TOOL_NAME,
                "arguments": json.dumps(_TOOL_ARGS, ensure_ascii=False),
            },
        }
    ]


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_POST(self) -> None:  # noqa: N802 (http.server 命名)
        length = int(self.headers.get("Content-Length", "0") or "0")
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw.decode("utf-8", errors="replace"))
        except json.JSONDecodeError:
            body = {}
        messages = body.get("messages") if isinstance(body, dict) else None
        first_round = not any(
            isinstance(m, dict) and m.get("role") == "tool" for m in (messages or [])
        )
        if isinstance(body, dict) and body.get("stream"):
            self._send_stream(first_round)
        else:
            self._send_json(first_round)

    def _message(self, first_round: bool) -> dict:
        if first_round:
            return {"role": "assistant", "content": None, "tool_calls": _tool_calls()}
        return {"role": "assistant", "content": _REPLY}

    def _send_json(self, first_round: bool) -> None:
        payload = {
            "id": "mock-llm",
            "object": "chat.completion",
            "created": 0,
            "model": "mock",
            "choices": [
                {
                    "index": 0,
                    "message": self._message(first_round),
                    "finish_reason": "tool_calls" if first_round else "stop",
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

    def _send_stream(self, first_round: bool) -> None:
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

        if first_round:
            # OpenAI SSE:tool_calls 以 index 增量下发(与 mock_openai._send_sse 同款)
            delta = {
                "role": "assistant",
                "content": None,
                "tool_calls": [{"index": i, **tc} for i, tc in enumerate(_tool_calls())],
            }
            self.wfile.write(chunk(delta, None))
            self.wfile.write(chunk({}, "tool_calls"))
        else:
            self.wfile.write(chunk({"role": "assistant", "content": _REPLY}, None))
            self.wfile.write(chunk({}, "stop"))
        self.wfile.write(b"data: [DONE]\n\n")

    def log_message(self, format: str, *args) -> None:  # noqa: A002
        pass


class FastBindHTTPServer(ThreadingHTTPServer):
    """HTTPServer.server_bind() 会用 socket.getfqdn(host) 反查 DNS；
    某些 CI runner(macOS)上该反查会卡住,导致 ready 行永远不打印。
    这里跳过反查:server_name 直接用 host。(与 mock_llm_reply.py 同款)"""

    def server_bind(self):
        socketserver.TCPServer.server_bind(self)
        host, port = self.server_address[:2]
        self.server_name = host
        self.server_port = port


def main() -> None:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 3466
    server = FastBindHTTPServer(("127.0.0.1", port), _Handler)
    actual_port = server.server_address[1]
    print(f"mock llm ready http://127.0.0.1:{actual_port}/v1", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
