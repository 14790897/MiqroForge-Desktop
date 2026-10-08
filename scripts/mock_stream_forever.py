"""Mock OpenAI-compatible server that streams reasoning deltas for a long time.

Unlike the other mocks in this directory — which build the whole SSE body and
send it with `Content-Length` — this one writes the stream **incrementally**
with `Transfer-Encoding: chunked`, so a turn can stay in flight for minutes
while the bridge → main → renderer path carries a steady event flow.  That is
the shape of the #1036 incident: a 58-minute turn that forwarded 111,982
reasoning chunks, during which a *subset* of RPCs (`config.get` /`plugins.list`)
ran out the full 720 s client timeout while other requests on the same channel
(`files.read`) were answered normally.

The turn must not be event-quiet: the incident's evidence rules out "the bridge
is simply busy" (identical requests, same window, some served), so an
experiment with a turn that produces no events cannot reproduce it.

Env:
  MIQI_MOCK_STREAM_SECONDS  how long to stream (default 900 = 15 min).  Long
                            enough that a request issued at the start can still
                            run out its 720 s timeout while the turn is alive.
  MIQI_MOCK_STREAM_RATE     reasoning deltas per second (default 30 — the
                            incident's rate was ~32/s over 58 min).

Run:  PYTHONPATH=. .venv/Scripts/python.exe scripts/mock_stream_forever.py 8899
"""

from __future__ import annotations

import json
import os
import socketserver
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DURATION_S = float(os.environ.get("MIQI_MOCK_STREAM_SECONDS", "900"))
RATE = float(os.environ.get("MIQI_MOCK_STREAM_RATE", "30"))

# One delta's worth of text — roughly the size real providers emit per chunk.
DELTA_TEXT = "让我再核对一下这一步的中间结果，确认参数与量纲都对得上。"


class Handler(BaseHTTPRequestHandler):
    # HTTP/1.1 + chunked so the body is delivered incrementally (see module doc).
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass

    # ── helpers ──────────────────────────────────────────────────────────

    def _json(self, status: int, obj: dict) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _chunk(self, payload: bytes) -> None:
        self.wfile.write(f"{len(payload):X}\r\n".encode("ascii") + payload + b"\r\n")
        self.wfile.flush()

    def _sse(self, obj: dict) -> None:
        self._chunk(("data: " + json.dumps(obj, ensure_ascii=False) + "\n\n").encode("utf-8"))

    def _chat_chunk(self, delta: dict, finish: str | None) -> dict:
        return {
            "id": "mock-stream",
            "object": "chat.completion.chunk",
            "created": 0,
            "model": "mock-model",
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
        }

    # ── routes ───────────────────────────────────────────────────────────

    def do_GET(self):
        if self.path.startswith("/v1/models"):
            self._json(200, {"object": "list", "data": [{"id": "mock-model", "object": "model"}]})
        else:
            self._json(404, {"error": {"message": "not found"}})

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b""
        try:
            req = json.loads(raw) if raw else {}
        except Exception:
            self._json(400, {"error": {"message": "bad json"}})
            return
        if not self.path.startswith("/v1/chat/completions"):
            self._json(404, {"error": {"message": "not found"}})
            return

        if not req.get("stream"):
            # Non-streaming callers get a single short answer (the provider
            # probes with stream=False on some paths).
            self._json(
                200,
                {
                    "id": "mock-stream",
                    "object": "chat.completion",
                    "created": 0,
                    "model": "mock-model",
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": "ok"},
                            "finish_reason": "stop",
                        }
                    ],
                },
            )
            return

        print(
            f"  [mock-stream] streaming {DURATION_S:.0f}s at {RATE:.0f} deltas/s",
            flush=True,
        )
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()

            # Role-only primer, exactly like OpenAI / DeepSeek open a stream with.
            self._sse(self._chat_chunk({"role": "assistant", "content": ""}, None))

            interval = 1.0 / RATE if RATE > 0 else 0.0
            deadline = time.monotonic() + DURATION_S
            sent = 0
            while time.monotonic() < deadline:
                self._sse(self._chat_chunk({"reasoning_content": DELTA_TEXT}, None))
                sent += 1
                if interval:
                    time.sleep(interval)
            self._sse(self._chat_chunk({}, "stop"))
            self._chunk(b"data: [DONE]\n\n")
            self._chunk(b"")  # terminating zero-length chunk
            print(f"  [mock-stream] done after {sent} deltas", flush=True)
        except (BrokenPipeError, ConnectionResetError):
            # Client aborted the turn (user pressed stop / app quit) — normal.
            print("  [mock-stream] client closed the stream early", flush=True)


class FastBindHTTPServer(ThreadingHTTPServer):
    """HTTPServer.server_bind() does a DNS reverse lookup of the host; on some
    CI runners that lookup hangs and the ready line never prints.  Skip it."""

    def server_bind(self):
        socketserver.TCPServer.server_bind(self)
        host, port = self.server_address[:2]
        self.server_name = host
        self.server_port = port


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8899
    server = FastBindHTTPServer(("127.0.0.1", port), Handler)
    actual_port = server.server_address[1]
    print(f"Mock stream server on http://127.0.0.1:{actual_port}/v1", flush=True)
    server.serve_forever()
