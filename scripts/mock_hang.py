"""Mock OpenAI-compatible server that NEVER responds — keeps a turn in flight.

Used by tool-error-neutral.spec.ts: the frontend registers its per-send chat
event listeners only while a turn is in flight, so the spec starts a real
send against this mock (which hangs forever) and then injects backend-style
progress events on the chat:progress IPC channel.

Run:  PYTHONPATH=. .venv/Scripts/python.exe scripts/mock_hang.py
"""
from __future__ import annotations

import json
import socketserver
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def do_GET(self):
        if self.path.startswith("/v1/models"):
            body = json.dumps(
                {"object": "list", "data": [{"id": "mock-model", "object": "model"}]}
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        if length:
            self.rfile.read(length)
        # Hang: never send a response.  The provider retries with backoff and
        # the turn stays in flight for the duration of the test.
        print("  [mock-hang] request received — hanging", flush=True)
        time.sleep(600)


class FastBindHTTPServer(ThreadingHTTPServer):
    """HTTPServer.server_bind() 会用 socket.getfqdn(host) 反查 DNS；
    某些 CI runner（macOS）上该反查会卡住，导致 ready 行永远不打印、
    serve_forever 永不执行。这里跳过反查：server_name 直接用 host。"""

    def server_bind(self):
        socketserver.TCPServer.server_bind(self)
        host, port = self.server_address[:2]
        self.server_name = host
        self.server_port = port


if __name__ == "__main__":
    import sys

    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8899
    server = FastBindHTTPServer(("127.0.0.1", port), Handler)
    actual_port = server.server_address[1]
    print(f"Mock hang server on http://127.0.0.1:{actual_port}/v1", flush=True)
    server.serve_forever()
