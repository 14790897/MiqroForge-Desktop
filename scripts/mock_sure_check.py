"""Mock OpenAI server — SURE 集成 GUI 验收专用脚本化模型。

按 assistant 已发生的 tool_call 计数分回合(与 scripts/mock_openai.py 同模式):
  R1(尚无 mcp_sure_sure_check 调用)→ 返回 tool_call #1: mcp_sure_sure_check,
      project=<env MIQI_SURE_PROJECT>(默认 PoC 测试项目绝对路径)。
  R2(已有 1 次调用)→ 返回 tool_call #2(同参数)——验证「永久允许」生效后
      第二次调用免弹窗。
  R3(已有 2 次调用)→ 取 messages 里最后一条 role=tool 的内容(真实 sure.exe
      输出),原文回贴为 assistant 文本 —— 验收可对报告原文做确定性断言。

副作用:把每次请求携带的 tools 名单合并写入 <env MIQI_SURE_TOOLS_FILE>
(默认 <cwd>/sure-mock-tools.json),供验收断言 5 个 mcp_sure_* 工具
确实进入了模型工具表。

Run: PYTHONPATH=. .venv/Scripts/python.exe scripts/mock_sure_check.py <port>
"""
from __future__ import annotations

import json
import os
import socketserver
from http.server import BaseHTTPRequestHandler, HTTPServer

DEFAULT_PROJECT = "D:\\Code\\MiQi\\sure-poc\\hello"
TOOLS_FILE = os.environ.get("MIQI_SURE_TOOLS_FILE") or os.path.join(os.getcwd(), "sure-mock-tools.json")


def _dump_tools(req: dict) -> None:
    """把**本次请求**的 tools 名单落盘(去重、排序)。

    记录最新一次请求而不是跨请求并集:TurnRunner 每轮携带同一份 tools
    (包含工具结果后的总结请求),但并集会掩盖「某次请求缺了某个 SURE 工具」
    这一事实——断言必须看最新请求(#1259 review)。
    """
    names = []
    for t in req.get("tools") or []:
        fn = t.get("function") or {}
        if fn.get("name"):
            names.append(fn["name"])
    if not names:
        return
    latest = sorted(set(names))
    try:
        with open(TOOLS_FILE, "w", encoding="utf-8") as f:
            json.dump(latest, f, ensure_ascii=False, indent=1)
    except Exception as e:  # noqa: BLE001 — 落盘失败不阻断回合
        print(f"  [mock-sure] tools dump failed: {e}", flush=True)


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

    def _send_sse(self, obj, streamed=True):
        """OpenAI SSE 格式(stream:true 必须走 SSE,平铺 JSON 在被测端=0 chunk)。"""
        msg = obj["choices"][0]["message"]
        tool_calls = msg.get("tool_calls")
        if tool_calls:
            delta = {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {"index": i, "id": tc["id"], "type": "function", "function": tc["function"]}
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

    def _respond(self, obj):
        if self._stream_requested:
            self._send_sse(obj)
        else:
            self._send(200, obj)

    def do_GET(self):
        if self.path.startswith("/v1/models"):
            self._send(200, {"object": "list", "data": [{"id": "deepseek-chat", "object": "model"}]})
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

        self._stream_requested = bool(req.get("stream"))
        _dump_tools(req)
        messages = req.get("messages", [])

        # 回合判定:assistant 历史里出现过几次 mcp_sure_sure_check 调用
        n_check = 0
        for m in messages:
            if m.get("role") == "assistant" and m.get("tool_calls"):
                for tc_ in m["tool_calls"]:
                    name = (tc_.get("function") or {}).get("name", "")
                    if name == "mcp_sure_sure_check":
                        n_check += 1

        def tc(name, args, cid="call_sure_check"):
            return {
                "id": "mock-tc",
                "object": "chat.completion",
                "created": 0,
                "model": "deepseek-chat",
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
                "id": "mock-final", "object": "chat.completion", "created": 0, "model": "deepseek-chat",
                "choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30},
            }

        project = os.environ.get("MIQI_SURE_PROJECT") or DEFAULT_PROJECT
        if n_check == 0:
            print(f"  [mock-sure] R1 → tool_call #1 mcp_sure_sure_check project={project}", flush=True)
            self._respond(tc("mcp_sure_sure_check", {"project": project}, "call_sure_check_1"))
            return
        if n_check == 1:
            # 第二次**同参数**调用:验证「永久允许」真的生效(不应再弹审批)。
            print("  [mock-sure] R2 → tool_call #2(同参数,验证免弹窗)", flush=True)
            self._respond(tc("mcp_sure_sure_check", {"project": project}, "call_sure_check_2"))
            return

        # R2:回贴真实工具结果原文
        tool_content = ""
        for m in reversed(messages):
            if m.get("role") == "tool":
                tool_content = str(m.get("content") or "")
                break
        print(f"  [mock-sure] R2 → 回贴工具结果({len(tool_content)} 字符)", flush=True)
        self._respond(text(
            "已完成核查。以下是 SURE 报告原文(逐字回贴,未做修改):\n\n" + tool_content
        ))


class FastBindHTTPServer(HTTPServer):
    """跳过 socket.getfqdn 反查(部分环境会卡住 ready 行)。"""

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
    print(f"Mock OpenAI server on http://127.0.0.1:{actual_port}/v1", flush=True)
    server.serve_forever()
