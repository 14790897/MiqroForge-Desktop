"""Deterministic OpenAI-compatible mock that drives a REAL skill pipeline run.

Round 1 — no ``exec`` in history: run the command carried in the latest user
message (``CMD: <command>``) via the exec tool with a 30-minute timeout.

Round 2 — exec done, no declaration yet: glob ``<dir>/*_report.md`` under the
``DECLARE_GLOB: <dir>`` directory from the user message and declare the found
report(s) with ``declare_result_files``.

Round 3 — plain text ``declared <n> report(s) (mock complete).``

The mock runs on the host with the same filesystem view as the app, so the
glob is authoritative — the spec never has to predict the skill's report name.
ASCII-only strings (CI runners may lack CJK glyphs).  Serves GET /v1/models
and POST /v1/chat/completions (``stream: true`` → SSE); prints its URL.
"""
import glob
import json
import os
import re
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

_CMD_RE = re.compile(r"CMD:\s*(.+)")
_GLOB_DIR_RE = re.compile(r"DECLARE_GLOB:\s*(.+)")


def _last_user(messages):
    return next(
        (str(m.get("content") or "") for m in reversed(messages) if m.get("role") == "user"),
        "",
    )


def _tool_names(messages):
    names = []
    for m in messages:
        if m.get("role") != "assistant":
            continue
        for tc in m.get("tool_calls") or []:
            names.append(((tc.get("function") or {}).get("name") or ""))
    return names


def _tool_call(call_id, name, args):
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)},
    }


def _reply(messages):
    user = _last_user(messages)
    cmd_m = _CMD_RE.search(user)
    glob_m = _GLOB_DIR_RE.search(user)
    # 录屏/演示场景：用户消息是自然语言，命令与声明目录经环境变量注入
    # （BVSE_CMD / BVSE_DECLARE_DIR），无需把内部分隔符写进消息。
    cmd_value = cmd_m.group(1).strip() if cmd_m else os.environ.get("BVSE_CMD", "").strip()
    glob_dir = (
        glob_m.group(1).strip().strip('"').strip("'")
        if glob_m
        else os.environ.get("BVSE_DECLARE_DIR", "").strip()
    )
    done = _tool_names(messages)

    if cmd_value and "exec" not in done:
        return _tool_calls_response(
            [_tool_call("call_exec", "exec", {"command": cmd_value, "timeout": 1800})]
        )

    if glob_dir and "declare_result_files" not in done:
        reports = sorted(glob.glob(os.path.join(glob_dir, "*_report.md")))
        if not reports:
            return _text("no report found (mock complete).")
        return _tool_calls_response(
            [_tool_call("call_declare", "declare_result_files", {"paths": reports})]
        )

    return _text("declared report (mock complete).")


def _text(text):
    return {
        "id": "chatcmpl-mock-final",
        "object": "chat.completion",
        "created": 0,
        "model": "mock",
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": text},
            "finish_reason": "stop",
        }],
        "usage": {"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30},
    }


def _tool_calls_response(calls):
    return {
        "id": "chatcmpl-mock-tool",
        "object": "chat.completion",
        "created": 0,
        "model": "mock",
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": None, "tool_calls": calls},
            "finish_reason": "tool_calls",
        }],
        "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
    }


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path.startswith("/v1/models"):
            self._json({"object": "list", "data": [{"id": "mock", "object": "model"}]})
            return
        self.send_response(404)
        self.end_headers()

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        try:
            req = json.loads(raw or b"{}")
        except ValueError:
            self._json({"error": {"message": "bad json"}})
            return
        obj = _reply(req.get("messages") or [])
        if req.get("stream"):
            self._sse(obj)
        else:
            self._json(obj)

    def _sse(self, obj):
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
            finish = "tool_calls"
        else:
            delta = {"role": "assistant", "content": msg.get("content") or ""}
            finish = "stop"
        chunks = [
            {"choices": [{"index": 0, "delta": delta, "finish_reason": None}]},
            {"choices": [{"index": 0, "delta": {}, "finish_reason": finish}]},
        ]
        body = "".join(
            "data: " + json.dumps(c, ensure_ascii=False) + "\n\n" for c in chunks
        ) + "data: [DONE]\n\n"
        self._send(body.encode("utf-8"), "text/event-stream")

    def _json(self, obj):
        self._send(json.dumps(obj, ensure_ascii=False).encode("utf-8"), "application/json")

    def _send(self, body, content_type):
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main():
    port = int(sys.argv[1])
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"http://127.0.0.1:{port}/v1", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
