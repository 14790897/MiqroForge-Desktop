"""Deterministic OpenAI-compatible mock that drives write_file × 5 + declare_result_files.

Round 1 — no tool call in history yet: five ``write_file`` calls for
``<tag>_report.md`` plus four process companions, where ``<tag>``
(``r1104_\\d+``) travels in the latest user message.

Round 2 — writes present, declaration absent: one ``declare_result_files``
call for the report only.  The declared form is taken from an optional
``DECLARE_AS=<path>`` directive in the latest user message; the spec uses it
to declare the **workspace-base-relative** form (``sessions/<key>/files/…``)
that the real agent emitted in #1131.  Without the directive the bare
filename is declared (#1104 behaviour).

Round 3 — declaration present: plain text ``declared <report> (mock complete).``

ASCII-only: the spec asserts on these strings and CI runners may lack CJK
glyphs.  Answers GET /v1/models and POST /v1/chat/completions in both wire
formats (``stream: true`` → SSE).  Prints its bound URL as
"http://127.0.0.1:<port>/v1".
"""
import json
import re
import socketserver
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

_TAG_RE = re.compile(r"r1104_\d{6,}")

_DECLARE_AS_RE = re.compile(r"DECLARE_AS=(\S+)")

_PROCESS_SUFFIXES = ("_script.py", "_data.json", "_trace.log", "_notes.md")


def _last_user(messages):
    return next(
        (str(m.get("content") or "") for m in reversed(messages) if m.get("role") == "user"),
        "",
    )


def _tool_names(messages):
    """All tool names already present in the conversation history."""
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


def _declared_path(messages, fallback):
    """Path form the mock declares for the report.

    ``DECLARE_AS=<path>`` in the latest user message wins; otherwise the bare
    filename written in round 1.  See the module docstring (#1131).
    """
    m = _DECLARE_AS_RE.search(_last_user(messages))
    return m.group(1) if m else fallback


def _reply(messages):
    tag_m = _TAG_RE.search(_last_user(messages))
    tag = tag_m.group(0) if tag_m else None
    if not tag:
        return _text("ok.")
    report = f"{tag}_report.md"
    done = _tool_names(messages)

    if "write_file" not in done:
        calls = [
            _tool_call("call_w0", "write_file", {"path": report, "content": "# report\n"})
        ] + [
            _tool_call(
                f"call_w{i + 1}",
                "write_file",
                {"path": f"{tag}{suffix}", "content": "x\n"},
            )
            for i, suffix in enumerate(_PROCESS_SUFFIXES)
        ]
        return _tool_calls_response(calls)

    if "declare_result_files" not in done:
        return _tool_calls_response(
            [
                _tool_call(
                    "call_declare",
                    "declare_result_files",
                    {"paths": [_declared_path(messages, report)]},
                )
            ]
        )

    return _text(f"declared {report} (mock complete).")


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


class FastBindHTTPServer(ThreadingHTTPServer):
    """HTTPServer.server_bind() 会用 socket.getfqdn(host) 反查 DNS；
    某些 CI runner（macOS）上该反查会卡住，导致 ready 行永远不打印、
    serve_forever 永不执行。这里跳过反查：server_name 直接用 host。"""

    def server_bind(self):
        socketserver.TCPServer.server_bind(self)
        host, port = self.server_address[:2]
        self.server_name = host
        self.server_port = port


def main():
    port = int(sys.argv[1])
    srv = FastBindHTTPServer(("127.0.0.1", port), Handler)
    print(f"http://127.0.0.1:{port}/v1", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
