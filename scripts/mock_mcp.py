"""Mock OpenAI-compatible server for the desktop MCP E2E (mcp.spec.ts).

Deterministic state machine driven by the request history:

  Round 1 (no MCP tool call yet): tool_call → mcp_e2emcp_e2e_echo
  Round 2 (tool result present): final text
    - "MCP-E2E-PASS：<tool output>" when the tool result contains the real
      MCP server's marker (scripts/mock_mcp_server.py ECHO_MARKER) — proves
      the MCP subprocess actually executed and its output round-tripped.
    - "MCP-E2E-FAIL：<tool output prefix>" otherwise (tool missing, error,
      or an unexpected result), so the spec can distinguish both cases.

The MiQi-wrapped tool name is ``mcp_<server>_<tool>`` — the E2E registers
the server under the name ``e2emcp``, so the wrapped name is
``mcp_e2emcp_e2e_echo``.

Run:  PYTHONPATH=. .venv/Scripts/python.exe scripts/mock_mcp.py
"""
from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, HTTPServer

MCP_TOOL_NAME = "mcp_e2emcp_e2e_echo"
ECHO_MARKER = "MCP_ECHO_RESULT_7f3a9c"


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def _send(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_sse(self, obj, streamed=True):
        """Streaming (stream:true) response in OpenAI SSE format.

        Mirrors scripts/mock_openai.py: one full message chunk + a finish
        chunk + [DONE].  A plain JSON body served to a stream:true request
        yields zero chunks and the provider completes with an empty response.
        """
        msg = obj["choices"][0]["message"]
        tool_calls = msg.get("tool_calls")
        if tool_calls:
            delta = {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "index": i,
                        "id": tc["id"],
                        "type": "function",
                        "function": tc["function"],
                    }
                    for i, tc in enumerate(tool_calls)
                ],
            }
            finish = obj["choices"][0].get("finish_reason") or "tool_calls"
        else:
            delta = {"role": "assistant", "content": msg.get("content") or ""}
            finish = obj["choices"][0].get("finish_reason") or "stop"
        chunk1 = {
            "id": obj.get("id", "mock-mcp"),
            "object": "chat.completion.chunk",
            "created": 0,
            "model": obj.get("model", "mock-mcp-model"),
            "choices": [{"index": 0, "delta": delta, "finish_reason": None}],
        }
        chunk2 = {
            "id": obj.get("id", "mock-mcp"),
            "object": "chat.completion.chunk",
            "created": 0,
            "model": obj.get("model", "mock-mcp-model"),
            "choices": [{"index": 0, "delta": {}, "finish_reason": finish}],
        }
        body = (
            "data: " + json.dumps(chunk1, ensure_ascii=False) + "\n\n"
            "data: " + json.dumps(chunk2, ensure_ascii=False) + "\n\n"
            "data: [DONE]\n\n"
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _respond(self, obj):
        if self._stream_requested:
            self._send_sse(obj)
        else:
            self._send(200, obj)

    def do_GET(self):
        if self.path.startswith("/v1/models"):
            self._send(200, {"object": "list", "data": [{"id": "mock-mcp-model", "object": "model"}]})
        else:
            self._send(404, {"error": {"message": "not found"}})

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length)
        try:
            req = json.loads(raw)
        except Exception:
            self._send(400, {"error": {"message": "bad json"}})
            return
        if not self.path.startswith("/v1/chat/completions"):
            self._send(404, {"error": {"message": "not found"}})
            return

        self._stream_requested = bool(req.get("stream"))
        messages = req.get("messages", [])

        # Tool results are role:"tool" messages whose content carries the
        # output string the orchestrator produced.
        tool_outputs = [
            str(m.get("content", ""))
            for m in messages
            if m.get("role") == "tool"
        ]
        n_mcp_calls = sum(
            1
            for m in messages
            if m.get("role") == "assistant"
            for tc in (m.get("tool_calls") or [])
            if (tc.get("function") or {}).get("name") == MCP_TOOL_NAME
        )

        def tc(name, args, cid="call_mcp"):
            return {
                "id": cid,
                "object": "chat.completion",
                "created": 0,
                "model": "mock-mcp-model",
                "choices": [{
                    "index": 0,
                    "message": {"role": "assistant", "content": None, "tool_calls": [{
                        "id": cid, "type": "function",
                        "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)},
                    }]},
                    "finish_reason": "tool_calls",
                }],
                "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
            }

        def text(content):
            return {
                "id": "mock-mcp-final", "object": "chat.completion", "created": 0,
                "model": "mock-mcp-model",
                "choices": [{"index": 0, "message": {"role": "assistant", "content": content},
                             "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30},
            }

        if n_mcp_calls == 0:
            print("  [mock-mcp] R1 → tool_call mcp_e2emcp_e2e_echo", flush=True)
            self._respond(tc(MCP_TOOL_NAME, {"text": "hello-mcp"}, "call_mcp_echo"))
            return

        joined = "\n".join(tool_outputs)
        if ECHO_MARKER in joined:
            print("  [mock-mcp] R2 → 工具返回含真实 MCP 标记 → PASS", flush=True)
            self._respond(text(f"MCP-E2E-PASS：工具链路成功。工具返回：{joined.strip()}"))
        else:
            print(f"  [mock-mcp] R2 → 未收到标记 → FAIL（收到 {len(tool_outputs)} 条工具结果）", flush=True)
            preview = joined.strip()[:200] if joined.strip() else "(无工具结果)"
            self._respond(text(f"MCP-E2E-FAIL：未收到预期工具返回。实际：{preview}"))


if __name__ == "__main__":
    import sys

    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8899
    server = HTTPServer(("127.0.0.1", port), Handler)
    actual_port = server.server_address[1]
    print(f"Mock OpenAI server on http://127.0.0.1:{actual_port}/v1", flush=True)
    server.serve_forever()
