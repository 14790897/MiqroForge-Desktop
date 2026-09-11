"""Mock OpenAI-compatible server for E2E: cross-session read regression (#921).

State machine:
  Round 1: tool_call → read_file on ANOTHER session's files dir.  The target
           path is embedded in the latest user message between
           ``__CROSS_READ_PATH_BEGIN__`` / ``__CROSS_READ_PATH_END__``
           markers (the test only learns the temp MIQI_HOME after launch).
           With the WSL sandbox active the real filesystem tool raises
           PermissionError (session isolation) from _resolve_sandbox_path —
           the orchestrator wraps it as ToolErrorEvent, which the UI must
           render as a neutral ⚠️ warning row, NOT the red error bubble
           reserved for turn-level errors.
  Round 2: final text — the turn ends normally (tool errors are recoverable
           by design; the model adapts).

Run:  PYTHONPATH=. .venv/Scripts/python.exe scripts/mock_cross_session_read.py
"""
from __future__ import annotations

import json
import time
from http.server import BaseHTTPRequestHandler, HTTPServer


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

    def _send_sse(self, obj):
        """Streaming (stream:true) response in OpenAI SSE format.

        Same contract as scripts/mock_openai.py: a plain JSON body served to
        a stream:true request yields zero chunks and the turn ends with no
        content.
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
            "id": obj.get("id", "mock"),
            "object": "chat.completion.chunk",
            "created": 0,
            "model": obj.get("model", "mock-model"),
            "choices": [{"index": 0, "delta": delta, "finish_reason": None}],
        }
        chunk2 = {
            "id": obj.get("id", "mock"),
            "object": "chat.completion.chunk",
            "created": 0,
            "model": obj.get("model", "mock-model"),
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

    def do_GET(self):
        if self.path.startswith("/v1/models"):
            self._send(200, {"object": "list", "data": [{"id": "mock-model", "object": "model"}]})
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

        streamed = bool(req.get("stream"))
        messages = req.get("messages", [])
        n_read = 0
        for m in messages:
            if m.get("role") != "assistant" or not m.get("tool_calls"):
                continue
            for tc in m["tool_calls"]:
                if (tc.get("function") or {}).get("name") == "read_file":
                    n_read += 1
        # Dump the last tool result so failures are diagnosable from the
        # test log (did read_file fail, or did it succeed?).
        tool_results = [
            str(m.get("content", ""))
            for m in messages
            if m.get("role") == "tool" and m.get("content")
        ]
        if tool_results:
            print(
                "  [mock] last tool result: " + tool_results[-1][:300].replace("\n", " "),
                flush=True,
            )
        # The cross-session target path arrives embedded in the LATEST user
        # message between markers (the test learns the temp MIQI_HOME only
        # after the app launches, so env/files cannot carry it at spawn time).
        # Plain string ops — a marker regex here trips CodeQL
        # py/polynomial-redos (the lazy group + \s* are quadratic).
        last_user = next(
            (str(m.get("content", "")) for m in reversed(messages) if m.get("role") == "user"),
            "",
        )
        begin_marker = "__CROSS_READ_PATH_BEGIN__"
        end_marker = "__CROSS_READ_PATH_END__"
        target = ""
        if begin_marker in last_user:
            start = last_user.index(begin_marker) + len(begin_marker)
            if end_marker in last_user[start:]:
                target = last_user[start : last_user.index(end_marker, start)].strip()

        if n_read == 0:
            print("  [mock] R1 → read_file 指向其他会话的 files 目录", flush=True)
            obj = {
                "id": "mock-read",
                "object": "chat.completion",
                "created": 0,
                "model": "mock-model",
                "choices": [{
                    "index": 0,
                    "message": {"role": "assistant", "content": None, "tool_calls": [{
                        "id": "call_cross_read",
                        "type": "function",
                        "function": {
                            "name": "read_file",
                            "arguments": json.dumps({"path": target}, ensure_ascii=False),
                        },
                    }]},
                    "finish_reason": "tool_calls",
                }],
                "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
            }
        else:
            # Small delay BEFORE the final reply: the test asserts the ⚠️
            # warning row while the turn is still in flight (the frontend
            # rebuilds the timeline from history once the turn completes).
            print("  [mock] R2 → 工具失败已反馈给模型，3s 后输出最终文本", flush=True)
            time.sleep(3)
            obj = {
                "id": "mock-final",
                "object": "chat.completion",
                "created": 0,
                "model": "mock-model",
                "choices": [{
                    "index": 0,
                    "message": {"role": "assistant", "content": "跨会话读取已被会话隔离策略拒绝，任务结束。"},
                    "finish_reason": "stop",
                }],
                "usage": {"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30},
            }

        if streamed:
            self._send_sse(obj)
        else:
            self._send(200, obj)


if __name__ == "__main__":
    import sys

    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8899
    server = HTTPServer(("127.0.0.1", port), Handler)
    actual_port = server.server_address[1]
    print(f"Mock cross-session-read server on http://127.0.0.1:{actual_port}/v1", flush=True)
    server.serve_forever()
