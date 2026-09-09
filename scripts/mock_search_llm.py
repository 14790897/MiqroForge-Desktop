"""Mock OpenAI-compatible server for zero-config search E2E (issue #979).

Deterministic two-round flow that drives the REAL web_search tool through
the desktop runtime:

  Round 1: tool_call → web_search (real tool: auto chain → zero-config DDGS)
  Round 2: web_search done → final text echoing the real result:
           "SEARCH_OK|{first result URL}"  (success)
           "SEARCH_FAILED|{error head}"    (the runtime reported failure)

The spec asserts the final reply marker, so the URL in the reply is the
URL the real DDGS backend actually returned — proof the zero-config chain
worked end-to-end inside the app.

Stdlib only (like mock_openai.py) — run with any python.
Run:  python scripts/mock_search_llm.py [port]
"""
from __future__ import annotations

import json
import re
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer

SEARCH_QUERY = "今天北京天气怎么样"
SEARCH_COUNT = 3


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

    def _respond(self, obj, streamed):
        if streamed:
            self._send_sse(obj)
        else:
            self._send(200, obj)

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

        def tc(name, args, cid="call_search"):
            return {
                "id": cid,
                "object": "chat.completion",
                "created": 0,
                "model": "mock-model",
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
                "id": "mock-final", "object": "chat.completion", "created": 0, "model": "mock-model",
                "choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30},
            }

        # 流程进度只看 assistant 侧的 web_search 调用次数（可靠，不看结果内容）
        n_search = 0
        for m in messages:
            if m.get("role") != "assistant" or not m.get("tool_calls"):
                continue
            for c in m["tool_calls"]:
                if (c.get("function") or {}).get("name") == "web_search":
                    n_search += 1

        if n_search == 0:
            print("  [mock-search] R1 → 真实执行 web_search", flush=True)
            self._respond(tc("web_search", {"query": SEARCH_QUERY, "count": SEARCH_COUNT}), streamed)
            return

        # R2：解析真实 web_search 工具结果，把证据嵌进最终回复
        tool_content = ""
        for m in reversed(messages):
            if m.get("role") == "tool":
                tool_content = str(m.get("content", ""))
                break
        m = re.search(r"https?://[^\s\"'`]+", tool_content)
        if tool_content.startswith("Error:") or "网络搜索失败" in tool_content:
            head = tool_content[:120].replace("\n", " ")
            print(f"  [mock-search] R2 → 搜索失败: {head}", flush=True)
            self._respond(text(f"SEARCH_FAILED|{head}"), streamed)
        elif m:
            print(f"  [mock-search] R2 → 搜索成功，首个结果 {m.group(0)}", flush=True)
            self._respond(text(f"SEARCH_OK|{m.group(0)}"), streamed)
        else:
            print(f"  [mock-search] R2 → 无 URL（内容: {tool_content[:80]}）", flush=True)
            self._respond(text("SEARCH_NO_URL|" + tool_content[:120].replace("\n", " ")), streamed)


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8899
    server = HTTPServer(("127.0.0.1", port), Handler)
    actual_port = server.server_address[1]
    print(f"Mock search LLM server on http://127.0.0.1:{actual_port}/v1", flush=True)
    server.serve_forever()
