"""Mock OpenAI-compatible server for the slurm billing live E2E.

确定性状态机（模型不是被测对象——被测的是真实平台网关 + RUNNING
检测 + Desktop 扣费 + UI 提示链路）：

  R1: tool_call → mcp_slurm_submit_slurm_job（真实网关提交 sleep 600
      长驻作业，保证后续轮询窗口内作业保持 RUNNING）
  R2..N: 从工具结果提取 job_id → tool_call → mcp_slurm_check_job_status
      （最多 15 次轮询，直到工具结果含 RUNNING）
  终态: 工具结果含 RUNNING → "DONE_SLURM"；
        提交失败 → "SUBMIT_FAILED：<预览>"（spec 据此区分失败原因）

Run:  PYTHONPATH=. .venv/Scripts/python.exe scripts/mock_slurm_billing.py <port>
"""
from __future__ import annotations

import json
import re
from http.server import BaseHTTPRequestHandler, HTTPServer

SUBMIT_TOOL = "mcp_slurm_submit_slurm_job"
CHECK_TOOL = "mcp_slurm_check_job_status"
MAX_CHECKS = 15

JOB_ID_RE = re.compile(r"\d{6,}")


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
                    {"index": i, "id": tc["id"], "type": "function", "function": tc["function"]}
                    for i, tc in enumerate(tool_calls)
                ],
            }
            finish = obj["choices"][0].get("finish_reason") or "tool_calls"
        else:
            delta = {"role": "assistant", "content": msg.get("content") or ""}
            finish = obj["choices"][0].get("finish_reason") or "stop"
        chunk1 = {
            "id": obj.get("id", "mock-slurm-billing"),
            "object": "chat.completion.chunk",
            "created": 0,
            "model": obj.get("model", "mock-slurm-billing-model"),
            "choices": [{"index": 0, "delta": delta, "finish_reason": None}],
        }
        chunk2 = {
            "id": obj.get("id", "mock-slurm-billing"),
            "object": "chat.completion.chunk",
            "created": 0,
            "model": obj.get("model", "mock-slurm-billing-model"),
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
            self._send(
                200,
                {"object": "list", "data": [{"id": "mock-slurm-billing-model", "object": "model"}]},
            )
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
        messages = req.get("messages", [])

        tool_outputs = [
            str(m.get("content", ""))
            for m in messages
            if m.get("role") == "tool"
        ]
        n_submits = sum(
            1
            for m in messages
            if m.get("role") == "assistant"
            for tc in (m.get("tool_calls") or [])
            if (tc.get("function") or {}).get("name") == SUBMIT_TOOL
        )
        n_checks = sum(
            1
            for m in messages
            if m.get("role") == "assistant"
            for tc in (m.get("tool_calls") or [])
            if (tc.get("function") or {}).get("name") == CHECK_TOOL
        )

        def tc(name, args, cid):
            return {
                "id": cid,
                "object": "chat.completion",
                "created": 0,
                "model": "mock-slurm-billing-model",
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
                "id": "mock-slurm-billing-final", "object": "chat.completion", "created": 0,
                "model": "mock-slurm-billing-model",
                "choices": [{"index": 0, "message": {"role": "assistant", "content": content},
                             "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30},
            }

        joined = "\n".join(tool_outputs)
        last_output = tool_outputs[-1] if tool_outputs else ""

        # R1：提交长驻作业（sleep 600——模型/轮询多慢都在 RUNNING 窗口内）
        if n_submits == 0:
            print("  [mock-slurm] R1 → submit_slurm_job（sleep 600 + hostname）", flush=True)
            self._respond(tc(
                SUBMIT_TOOL,
                {"script": "#!/bin/bash\nsleep 600\nhostname", "partition": "vip_192_al"},
                "call_submit",
            ))
            return

        # 提交失败（网关/集群拒绝）→ 终止并携带预览
        if "error" in last_output.lower() or "failed" in last_output.lower():
            if n_checks == 0:
                print(f"  [mock-slurm] 提交失败 → SUBMIT_FAILED：{last_output[:120]}", flush=True)
                self._respond(text(f"SUBMIT_FAILED：{last_output.strip()[:300]}"))
                return

        # R2..N：提取 job_id，轮询到 RUNNING
        if "RUNNING" in last_output:
            print("  [mock-slurm] 工具返回 RUNNING → DONE_SLURM", flush=True)
            self._respond(text("DONE_SLURM"))
            return

        if n_checks >= MAX_CHECKS:
            print(f"  [mock-slurm] 超过 {MAX_CHECKS} 次轮询仍未 RUNNING → CHECK_TIMEOUT", flush=True)
            self._respond(text(f"CHECK_TIMEOUT：最后状态 {last_output.strip()[:200]}"))
            return

        m = JOB_ID_RE.search(joined)
        job_id = m.group(0) if m else ""
        print(f"  [mock-slurm] R{n_checks + 2} → check_job_status(job_id={job_id})", flush=True)
        self._respond(tc(CHECK_TOOL, {"job_id": job_id}, f"call_check_{n_checks}"))


if __name__ == "__main__":
    import sys

    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8899
    server = HTTPServer(("127.0.0.1", port), Handler)
    actual_port = server.server_address[1]
    print(f"Mock OpenAI server on http://127.0.0.1:{actual_port}/v1", flush=True)
    server.serve_forever()
