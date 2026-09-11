"""Mock OpenAI-compatible server that ALWAYS fails with a Chinese error body.

Used by bridge-chinese-error.spec.ts to reproduce the UnicodeEncodeError
crash: a provider error containing Chinese text gets logged by the bridge to
stderr; on Windows locales where a piped stderr defaults to ASCII, the bridge
died with "'ascii' codec can't encode characters" instead of surfacing the
real error. The desktop fix forces PYTHONIOENCODING=utf-8 on the bridge spawn
(and miqi/bridge/server.py reconfigures stdio), so this mock's Chinese body
flows through logging unharmed.

Run: python scripts/mock_openai_error.py [port]
"""
from __future__ import annotations

import json
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer

# Unique marker the E2E spec asserts on — change both together.
ERROR_MESSAGE = "模拟服务故障（中文错误消息）：模型网关返回 500，请稍后重试。"


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def _send(self, code: int, obj: dict) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        print(f"  [mock-err] GET {self.path}", flush=True)
        if self.path.startswith("/v1/models"):
            self._send(200, {"object": "list", "data": [{"id": "mock-error-model", "object": "model"}]})
        else:
            self._send(404, {"error": {"message": "not found"}})

    def do_POST(self):
        # Every chat.completions call fails with the Chinese error body.
        print(f"  [mock-err] POST {self.path}", flush=True)
        self._send(500, {"error": {"message": ERROR_MESSAGE}})


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8899
    server = HTTPServer(("127.0.0.1", port), Handler)
    # Print the ACTUAL bound port — with port 0 it differs from argv.
    print(f"Mock OpenAI error server on http://127.0.0.1:{server.server_address[1]}/v1", flush=True)
    server.serve_forever()
