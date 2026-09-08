"""Minimal deterministic OpenAI-compatible mock (delete-all focus E2E).

Serves a single assistant text chunk + finish + [DONE] for every chat
completions request, plus GET /v1/models for the provider check.  Prints its
bound URL as "http://127.0.0.1:<port>/v1".

Self-contained on purpose: seeding a conversation here must not require the
confirm-card state machine in scripts/mock_openai.py.
"""
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

CHUNK = {
    "id": "chatcmpl-mock",
    "object": "chat.completion.chunk",
    "model": "mock",
    "choices": [{"index": 0, "delta": {"content": "ok，收到。"}, "finish_reason": None}],
}
FINISH = {
    "id": "chatcmpl-mock",
    "object": "chat.completion.chunk",
    "model": "mock",
    "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _sse(self):
        body = (
            b"data: "
            + json.dumps(CHUNK, ensure_ascii=False).encode("utf-8")
            + b"\n\ndata: "
            + json.dumps(FINISH, ensure_ascii=False).encode("utf-8")
            + b"\n\ndata: [DONE]\n\n"
        )
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.startswith("/v1/models"):
            self._json({"object": "list", "data": [{"id": "mock", "object": "model"}]})
            return
        self.send_response(404)
        self.end_headers()

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length:
            self.rfile.read(length)
        self._sse()

    def _json(self, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
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
