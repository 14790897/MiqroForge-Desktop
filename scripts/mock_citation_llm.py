"""Mock OpenAI-compatible server for #879 citation-footnote E2E (real data).

Two-round flow that drives the REAL web_search tool, then writes a [n]
footnote + 参考文献 list from the REAL result (title + URL) — proof that the
citation binding works on real sources, not hardcoded placeholders.

  Round 1: tool_call → web_search (real: auto chain → zero-config DDGS)
  Round 2: parse the real result's title + URL, reply:
           "查询结果：{title}[1]。\\n\\n## 参考文献\\n[1] {title}；{url}"

Stdlib only (like mock_openai.py) — run with any python.
Run:  python scripts/mock_citation_llm.py [port]
"""
from __future__ import annotations

import json
import re
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer

SEARCH_QUERY = "MOF 金属有机框架 比表面积"
SEARCH_COUNT = 3


def _parse_first_result(tool_content: str):
    """从 web_search 结果文本（"1. title\\n   url\\n   snippet"）解析首条 title + url。"""
    title = ""
    url = ""
    for line in tool_content.split("\n"):
        s = line.strip()
        if not title:
            # \S 打头避免 `\s*` 与 `.+` 的重叠回溯（CodeQL ReDoS）。
            m = re.match(r"^\d+\.\s*(\S.*)$", s)
            if m:
                title = m.group(1).strip()
                continue
        if not url:
            m = re.search(r"https?://[^\s\"'`]+", s)
            if m:
                url = m.group(0).rstrip(".,;:!?")
                continue
        if title and url:
            break
    return title, url


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

        # 只看 assistant 侧 web_search 调用次数（可靠，不看结果内容）
        n_search = 0
        for m in messages:
            if m.get("role") != "assistant" or not m.get("tool_calls"):
                continue
            for c in m["tool_calls"]:
                if (c.get("function") or {}).get("name") == "web_search":
                    n_search += 1

        if n_search == 0:
            print("  [mock-citation] R1 → 真实执行 web_search", flush=True)
            self._respond(tc("web_search", {"query": SEARCH_QUERY, "count": SEARCH_COUNT}), streamed)
            return

        # R2：解析真实 web_search 结果，用真实 title + url 写脚注 + 参考文献
        tool_content = ""
        for m in reversed(messages):
            if m.get("role") == "tool":
                tool_content = str(m.get("content", ""))
                break
        title, url = _parse_first_result(tool_content)
        if title and url:
            print(f"  [mock-citation] R2 → 真实结果 {title[:40]} | {url}", flush=True)
            answer = f"查询结果：{title}[1]。\n\n## 参考文献\n[1] {title}；{url}"
            self._respond(text(answer), streamed)
        elif url:
            print(f"  [mock-citation] R2 → 仅有 URL {url}", flush=True)
            answer = f"查询结果[1]。\n\n## 参考文献\n[1] 检索结果；{url}"
            self._respond(text(answer), streamed)
        else:
            head = tool_content[:120].replace("\n", " ")
            print(f"  [mock-citation] R2 → 搜索失败/无结果: {head}", flush=True)
            self._respond(text("CITATION_NO_RESULT|" + head), streamed)


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8899
    server = HTTPServer(("127.0.0.1", port), Handler)
    actual_port = server.server_address[1]
    print(f"Mock citation LLM server on http://127.0.0.1:{actual_port}/v1", flush=True)
    server.serve_forever()
