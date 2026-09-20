"""Minimal deterministic OpenAI-compatible mock that drives ONE create_pdf call.

Round 1 — the LATEST user message names a ``*.pdf`` file and no create_pdf
call is in the request history yet: answer with a single ``create_pdf``
tool_call for that filename (the filename travels in the user message, so the
spec never has to tell the mock anything out of band).

Round 2 — only when the latest user message carries a ``DECLARE_AS=<path>``
directive and create_pdf already happened: answer with one
``declare_result_files`` call for that path.  This mirrors the real agent in
#1131, which declared the **workspace-base-relative** form
(``sessions/<key>/files/<name>``).  Without the directive this round is
skipped entirely, so the #983 spec's flow is unchanged.

Every other request (no ``*.pdf`` in the latest user message, or the calls
already happened) answers with plain text ``created <file> (mock complete).``.

ASCII-only on purpose: the spec asserts on this string, and CI runners may
lack a CJK font, so neither the reply text nor the PDF title carries CJK.

Answers GET /v1/models and POST /v1/chat/completions in both wire formats —
``stream: true`` is served as SSE, exactly like scripts/mock_openai.py (a
plain JSON body to a streaming request yields zero chunks and the turn ends
empty).  Prints its bound URL as "http://127.0.0.1:<port>/v1".

Self-contained on purpose: exercising the doc-tool tracked store must not
depend on the confirm-card state machine in scripts/mock_openai.py.
"""
import json
import re
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# The filename the spec puts into the user message (ASCII or CJK, one path
# segment).  \w is Unicode-aware in Python, so 报告.pdf matches too.
# Bounded quantifier and NO '.' inside the class: the class cannot overlap the
# literal '\.pdf' that follows, so scanning is linear on arbitrary message
# text (CodeQL py/polynomial-redos).  A dotted name like a.v2.pdf still
# resolves to its last segment — fine, the spec owns the filename.
_PDF_RE = re.compile(r"[\w-]{1,64}\.pdf")

_DECLARE_AS_RE = re.compile(r"DECLARE_AS=(\S+)")

_TOOL_NAME = "create_pdf"

_DECLARE_TOOL_NAME = "declare_result_files"


def _last_user(messages):
    return next(
        (str(m.get("content") or "") for m in reversed(messages) if m.get("role") == "user"),
        "",
    )


def _already_called(messages, name):
    """True when the conversation already carries a tool_call named *name*."""
    for m in messages:
        if m.get("role") != "assistant":
            continue
        for tc in m.get("tool_calls") or []:
            if ((tc.get("function") or {}).get("name") or "") == name:
                return True
    return False


def _already_created_pdf(messages):
    """create_pdf under either its current or its historical tool name."""
    return _already_called(messages, _TOOL_NAME) or _already_called(
        messages, "pdf_write"
    )


def _reply(messages):
    """Pick the response for this request: tool_call or plain text."""
    user = _last_user(messages)
    filename = None
    m = _PDF_RE.search(user)
    if m:
        # Must be set BEFORE the tool-call branch: later rounds take the text
        # branch and report the same filename back.
        filename = m.group(0)
    if m and not _already_created_pdf(messages):
        args = {
            "filename": filename,
            "title": "#983 panel e2e",
            "content": [{"type": "paragraph", "text": "created by create_pdf_mock.py"}],
        }
        return _tool_call_response(
            "call_create_pdf", _TOOL_NAME, args,
        )
    declare_as = _DECLARE_AS_RE.search(user)
    if (
        declare_as
        and filename
        and not _already_called(messages, _DECLARE_TOOL_NAME)
    ):
        return _tool_call_response(
            "call_declare", _DECLARE_TOOL_NAME, {"paths": [declare_as.group(1)]},
        )
    text = f"created {filename} (mock complete)." if filename else "ok."
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


def _tool_call_response(call_id, name, args):
    return {
        "id": "chatcmpl-mock-tool",
        "object": "chat.completion",
        "created": 0,
        "model": "mock",
        "choices": [{
            "index": 0,
            "message": {
                "role": "assistant",
                "content": None,
                "tool_calls": [{
                    "id": call_id,
                    "type": "function",
                    "function": {
                        "name": name,
                        "arguments": json.dumps(args, ensure_ascii=False),
                    },
                }],
            },
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
        """Streaming response: one full-message chunk + finish chunk + [DONE]."""
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
