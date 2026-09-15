"""Deterministic OpenAI-compatible mock for #879 citation-footnote E2E.

Serves a single assistant text chunk containing `[n]` footnotes + a 参考文献
list (作者/标题/期刊/年份/DOI), so the frontend parses them and renders `[n]`
as a clickable citation that opens a source-detail modal. No tool calls / no
search / no approvals — self-contained on purpose (mirrors plain_reply_mock.py).
"""
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

REPLY = (
    "关键数据「冷冻造粒基本零 BET 损失」[1]，介孔氧化铝成型损失约 8.7%[2]。\n\n"
    "## 参考文献\n"
    "[1] 张三；MOF 造粒工艺综述；材料学报；2023；https://doi.org/10.1016/j.matt.2023.01.001\n"
    "[2] 王五；介孔氧化铝成型损失研究；化工进展；2020；https://example.com/alumina\n"
)

CHUNK = {
    "id": "chatcmpl-mock",
    "object": "chat.completion.chunk",
    "model": "mock",
    "choices": [{"index": 0, "delta": {"content": REPLY}, "finish_reason": None}],
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
