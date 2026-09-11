"""Mock OpenAI-compatible server for desktop E2E (issue #646) — full flow.

State machine driven by tool results already in the request history:

  Round 1: tool_call → ask_user_confirm_card #1 (确认执行方案?, 4 steps)
  Round 2: confirmed → tool_call → web_search (real tool, actually runs)
  Round 3: web_search done → tool_call → write_file (WorkflowDefinition JSON)
  Round 4: file written → tool_call → ask_user_confirm_card #2 (是否上传到 MiQroForge?)
  Round 5: confirmed → final text (uploaded + project link)

Dual-card branch (issue #714): when the latest user message contains the
"双卡" trigger, ONE response carries TWO ask_user_confirm_card tool_calls in
the same turn (确认发起网络搜索？ / 确认创建文档？), then a deterministic
final text once the tool results come back. This reproduces the stacked-card
scenario on the legacy concurrent dispatch path.

Run:  PYTHONPATH=. .venv/Scripts/python.exe scripts/mock_openai.py
"""
from __future__ import annotations

import json
import os
import re
from http.server import BaseHTTPRequestHandler, HTTPServer

EXEC_TITLE = "确认执行方案？"
EXEC_MESSAGE = "我将执行 4 个步骤（涉及外网论文下载与价格查询），开始前需要你确认。"
EXEC_STEPS = [
    {"id": "search_papers", "title": "搜索并下载相关论文"},
    {"id": "extract_info", "title": "提取 MOF-5 合成路线与成本信息"},
    {"id": "query_price", "title": "查询供应商价格（国内）"},
    {"id": "generate_report", "title": "生成最终报告"},
]
EXEC_CHOICES = [
    {"id": "confirm", "label": "确认执行"},
    {"id": "adjust", "label": "调整方案"},
    {"id": "cancel", "label": "取消"},
]

# issue #714 dual-card scenario (same turn, one response, two cards)
DUAL_TITLE_A = "确认发起网络搜索？"
DUAL_TITLE_B = "确认创建文档？"

UPLOAD_TITLE = "方案已完成，是否上传到 MiQroForge？"
UPLOAD_MESSAGE = "工作流方案已生成并通过校验，上传后将作为 WorkflowDefinition 发布到 MiQroForge 平台。"
UPLOAD_CHOICES = [
    {"id": "confirm", "label": "确认上传"},
    {"id": "cancel", "label": "取消"},
]

def _build_steps(text: str) -> list[dict]:
    """动态生成步骤：解析用户调整要求（步数/市场/复杂度），模拟 LLM 理解。"""

    n = 3
    # Bounded digit run — avoids CodeQL py/polynomial-redos on long inputs.
    m = re.search(r"(\d{1,4})\s*步", text[:200])
    if m:
        n = max(3, min(int(m.group(1)), 10))
    market = "海外" if ("海外" in text or "国外" in text or "全球" in text) else ("国内" if "国内" in text else "市场")
    complex_ = any(k in text for k in ("复杂", "足够", "完整", "详细", "全面"))
    if complex_ and n < 5:
        n = 5  # 要求"复杂/完整"时至少 5 步

    steps = [
        {"id": "search_lit", "title": f"搜索{market}文献（MOF-5 合成）"},
        {"id": "extract", "title": f"提取{market}合成路线与成本"},
    ]
    if n >= 4:
        steps.append({"id": "benchmark", "title": f"对比{market}主流合成工艺（溶剂/温度/产率）"})
    if n >= 5:
        steps.append({"id": "sensitivity", "title": "成本敏感性分析（原料价格波动影响）"})
    if complex_ and n >= 6:
        steps.append({"id": "validate", "title": "交叉验证数据来源与精度"})
    steps.append({"id": "report", "title": "生成最终报告"})
    extras = ["供应商报价", "纯度与等级", "供应链风险", "政策与环保影响"]
    while len(steps) < n:
        steps.append({"id": f"step_extra_{len(steps)}", "title": f"补充分析：{extras[len(steps) % len(extras)]}"})
    return steps[:max(n, 3)]


WORKFLOW_JSON = {
    "spec_version": "1.0.0",
    "document_kind": "workflow_definition",
    "metadata": {
        "id": "com.acme.mof-price-report",
        "name": "mof-price-report",
        "title": "MOF-5 市场合成价格报告",
        "version": "1.0.0",
        "description": "查询 MOF-5 合成成本与市场价格并生成报告",
    },
    "interface": {},
    "activation": {"policy": "explicit"},
    "inputs": [],
    "parameters": [],
    "execution": {
        "default_mode": "full",
        "entrypoints": [{"id": "run", "mode": "full", "executor": {"type": "shell"}}],
        "backend_policy": {"strategy": "fixed", "backends": [{"id": "local", "kind": "local"}]},
    },
    "graph": {
        "nodes": [
            {"id": "step1", "title": "步骤一", "kind": "task", "executor": {"type": "shell"},
             "presentation": {"data_view": {}, "action_view": {}}},
            {"id": "step2", "title": "步骤二", "kind": "task", "executor": {"type": "shell"},
             "presentation": {"data_view": {}, "action_view": {}}},
            {"id": "step3", "title": "步骤三", "kind": "task", "executor": {"type": "shell"},
             "presentation": {"data_view": {}, "action_view": {}}},
            {"id": "step4", "title": "步骤四", "kind": "task", "executor": {"type": "shell"},
             "presentation": {"data_view": {}, "action_view": {}}},
        ],
        "edges": [],
    },
    "completion": [{"id": "done", "description": "完成", "severity": "success"}],
}


def _tool_result(messages):
    """Collect (tool_name, output) of the LAST ask_user_confirm_card result."""
    results = []
    for m in messages:
        if m.get("role") == "tool":
            try:
                parsed = json.loads(m.get("content", "{}"))
                # Keep only dict-shaped results — web_search/write_file may
                # return JSON arrays or strings (issue #646 review).
                if isinstance(parsed, dict):
                    results.append(parsed)
            except Exception:
                results.append({})
    return results


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
        """Streaming (stream:true) response in OpenAI SSE format.

        The OpenAI SDK only parses `text/event-stream` bodies: a plain JSON
        body served to a stream:true request yields zero chunks and the
        provider completes with an empty response (desktop turns then end
        with no content). Emit one full message chunk + a finish chunk +
        [DONE], matching real OpenAI-compatible providers.
        """
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

    def _respond(self, obj):
        """Respond to a chat.completions call in the requested format."""
        if self._stream_requested:
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

        # stream:true responses must be SSE — plain JSON yields 0 chunks.
        self._stream_requested = bool(req.get("stream"))
        messages = req.get("messages", [])
        results = _tool_result(messages)

        # 用 assistant 消息里的 tool_call 序列判断流程进度（可靠，不看结果内容）
        calls_seen: list[str] = []
        for m in messages:
            if m.get("role") == "assistant" and m.get("tool_calls"):
                for tc in m["tool_calls"]:
                    name = (tc.get("function") or {}).get("name", "")
                    if name:
                        calls_seen.append(name)
        n_search = calls_seen.count("web_search")
        n_write = calls_seen.count("write_file")
        n_read = calls_seen.count("read_file")
        n_confirm_cards = sum(1 for r in results if r.get("status") == "confirmed")

        def tc(name, args, cid="call_x"):
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

        def tc_multi(pairs):
            """One response whose message carries MULTIPLE tool_calls
            (issue #714: same-turn stacked confirm cards)."""
            return {
                "id": "mock-multi", "object": "chat.completion", "created": 0, "model": "mock-model",
                "choices": [{
                    "index": 0,
                    "message": {"role": "assistant", "content": None, "tool_calls": [
                        {
                            "id": cid, "type": "function",
                            "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)},
                        }
                        for name, args, cid in pairs
                    ]},
                    "finish_reason": "tool_calls",
                }],
                "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
            }

        # ── issue #864 write-authorization card branch ──
        # Triggered by the LATEST user message containing "写授权" so it never
        # collides with the main flow. The model emits ONE response carrying a
        # single write_file tool_call whose target is a workspace-EXTERNAL
        # absolute path (a temp dir on the host) and declares it via
        # authorize_paths — the desktop must pop the write-authorization card
        # (允许本次 / 本目录不再询问 / 拒绝) instead of silently writing.
        last_user = next(
            (str(m.get("content", "")) for m in reversed(messages) if m.get("role") == "user"),
            "",
        )
        if "写授权" in last_user:
            if n_write > 0:
                self._respond(text("写授权流程结束。"))
                return
            out_dir = os.environ.get("MIQI_AUTH_OUT_DIR", "")
            target = os.path.join(out_dir, "auth_probe.txt") if out_dir else ""
            if not target:
                self._respond(text("写授权：缺少 MIQI_AUTH_OUT_DIR 环境变量。"))
                return
            self._respond(tc("write_file", {
                "path": target,
                "content": "authorization-card-e2e-probe",
                "authorize_paths": [target],
            }, "call_auth_write"))
            return

        # ── issue #714 dual-card branch（同一回合两张确认卡） ──
        # Triggered by the LATEST user message containing "双卡" so it never
        # collides with the main flow; counts only dual-titled cards already
        # emitted so the branch is deterministic even with shared history.
        n_dual_calls = 0
        for m in messages:
            if m.get("role") != "assistant" or not m.get("tool_calls"):
                continue
            for call in m["tool_calls"]:
                fn = call.get("function") or {}
                if fn.get("name") == "ask_user_confirm_card" and DUAL_TITLE_A in str(fn.get("arguments", "")):
                    n_dual_calls += 1
        if "双卡" in last_user:
            if n_dual_calls == 0:
                print("  [mock] 双卡回合 → 单响应两张确认卡")
                self._respond(tc_multi([
                    ("ask_user_confirm_card", {
                        "title": DUAL_TITLE_A,
                        "message": "将在互联网上搜索相关信息。",
                        "choices": [
                            {"id": "confirm", "label": "确认搜索"},
                            {"id": "cancel", "label": "取消", "role": "cancel"},
                        ],
                        "timeout_seconds": 120,
                    }, "call_dual_search"),
                    ("ask_user_confirm_card", {
                        "title": DUAL_TITLE_B,
                        "message": "将创建一份新的工作文档。",
                        "choices": [
                            {"id": "confirm", "label": "确认创建"},
                            {"id": "cancel", "label": "取消", "role": "cancel"},
                        ],
                        "timeout_seconds": 120,
                    }, "call_dual_doc"),
                ]))
            else:
                print("  [mock] 双卡回合结束")
                self._respond(text("双卡流程结束：两张确认卡均已处理完毕。"))
            return

        # ── #646-v2 plan-card branch（用户消息含"计划"）──────────────
        # 独立状态机：ask_user_plan_confirm → web_search → write_file →
        # request_action_confirmation（ActionCard）→ 完成
        n_plan = calls_seen.count("ask_user_plan_confirm")
        n_action = calls_seen.count("request_action_confirmation")
        last = results[-1] if results else {}
        # ── auto 分支（用户消息含"自动"）——不弹卡，直接执行序列 ──
        if "自动" in last_user:
            n_search = calls_seen.count("list_dir")
            n_write = calls_seen.count("write_file")
            n_action = calls_seen.count("request_action_confirmation")
            if n_search == 0:
                # 用 list_dir（本地快、READ 阶段）替代 web_search——E2E 里
                # 真实网络搜索慢会卡住回合（web_search 是真实工具非 mock）
                print("  [mock] Auto 分支 → list_dir", flush=True)
                self._respond(tc("list_dir", {"path": "."}, "call_a_search"))
                return
            if n_write == 0:
                print("  [mock] Auto 分支 → write_file", flush=True)
                self._respond(tc("write_file", {
                    "path": "mof-report.json",
                    "content": json.dumps({"title": "MOF-5 调研报告", "findings": []}, ensure_ascii=False),
                }, "call_a_write"))
                return
            if n_action == 0:
                print("  [mock] Auto 分支 → ActionCard", flush=True)
                self._respond(tc("request_action_confirmation", {
                    "action": "upload", "target": "Qraft", "file_name": "mof-report.json",
                    "size_bytes": 23552, "sha256": "deadbeef1234567890abcdef1234567890",
                    "description": "上传 MOF-5 实验报告到 Qraft",
                }, "call_a_action"))
                return
            print("  [mock] Auto 分支 → 完成", flush=True)
            self._respond(text("✅ 已完成：MOF-5 实验报告已生成并上传 Qraft。"))
            return
        if "计划" in last_user:
            if n_plan == 0:
                print("  [mock] PlanCard 分支 → ask_user_plan_confirm", flush=True)
                self._respond(tc("ask_user_plan_confirm", {
                    "title": "生成 MOF-5 实验报告",
                    "goal": "搜索论文并生成报告，上传到 Qraft",
                    "steps": [
                        {"name": "搜集论文资料", "tools": ["web_search"]},
                        {"name": "创建实验报告", "tools": ["write_file"]},
                        {"name": "上传到 Qraft", "tools": ["upload"]},
                    ],
                    "permissions": ["network_read", "workspace_write", "external_upload"],
                    "timeout_seconds": 120,
                }, "call_plan"))
                return
            if last.get("choice_id") == "modify":
                print("  [mock] 计划 modify → 重新规划弹新卡", flush=True)
                self._respond(tc("ask_user_plan_confirm", {
                    "title": "生成 MOF-5 实验报告（修改版）",
                    "goal": "按用户意见调整：增加成本对比步骤",
                    "steps": [
                        {"name": "搜集论文资料", "tools": ["web_search"]},
                        {"name": "对比合成成本", "tools": ["web_search"]},
                        {"name": "创建实验报告", "tools": ["write_file"]},
                        {"name": "上传到 Qraft", "tools": ["upload"]},
                    ],
                    "permissions": ["network_read", "workspace_write", "external_upload"],
                    "timeout_seconds": 120,
                }, "call_plan2"))
                return
            if n_plan >= 1 and n_search == 0:
                print("  [mock] PlanCard 确认 → R2 web_search", flush=True)
                self._respond(tc("web_search", {"query": "MOF-5 metal-organic framework synthesis", "max_results": 3}, "call_search2"))
                return
            if n_plan >= 1 and n_write == 0:
                print("  [mock] R3 → write_file", flush=True)
                self._respond(tc("write_file", {
                    "path": "mof-report.json",
                    "content": json.dumps({"title": "MOF-5 调研报告", "findings": []}, ensure_ascii=False),
                }, "call_write2"))
                return
            if n_plan >= 1 and n_action == 0:
                print("  [mock] R4 → ActionCard（上传确认）", flush=True)
                self._respond(tc("request_action_confirmation", {
                    "action": "upload", "target": "Qraft", "file_name": "mof-report.json",
                    "size_bytes": 23552, "sha256": "deadbeef1234567890abcdef1234567890",
                    "description": "上传 MOF-5 实验报告到 Qraft",
                }, "call_action"))
                return
            if n_plan >= 1 and n_action >= 1:
                print("  [mock] R5 → 完成", flush=True)
                self._respond(text("✅ 已完成：MOF-5 实验报告已生成并上传 Qraft。"))
                return

        # ── state machine（按工具调用序列推进） ──
        if not results:
            print("  [mock] R1 → 确认执行方案卡（4 步骤）")
            self._respond(tc("ask_user_confirm_card", {
                "title": EXEC_TITLE, "message": EXEC_MESSAGE,
                "steps": EXEC_STEPS, "choices": EXEC_CHOICES,
                "timeout_seconds": 60, "allow_remember_choice": True,
            }, "call_exec_confirm"))
            return

        last = results[-1] if results else {}
        last_role = messages[-1].get("role") if messages else ""
        had_adjust = any(r.get("choice_id") == "adjust" for r in results)

        # 调整闭环：用户已点"调整方案"并输入了调整内容 → 弹调整后方案卡
        if had_adjust and last_role == "user":
            user_text = str(messages[-1].get("content", "")).strip()
            print(f"  [mock] 收到调整输入「{user_text[:30]}」→ 动态生成方案", flush=True)
            steps = _build_steps(user_text)
            self._respond(tc("ask_user_confirm_card", {
                "title": EXEC_TITLE,
                "message": f"已按你的要求调整（{user_text[:60]}），请再次确认。",
                "steps": steps, "choices": EXEC_CHOICES,
                "timeout_seconds": 60,
            }, "call_exec_confirm_2"))
            return

        # 刚点了"调整方案" → 引导用户输入调整内容（卡片不承担 controller）
        if last.get("choice_id") == "adjust":
            print("  [mock] 用户选择调整 → 引导输入", flush=True)
            self._respond(text(
                "好的，请告诉我如何调整方案。\n"
                "例如：市场改为海外、文献限定 2 篇、只保留前 3 步……"
            ))
            return

        if last.get("status") == "cancelled":
            print(f"  [mock] 用户取消（{last.get('reason','')}）→ 结束", flush=True)
            self._respond(text("好的，已取消。需要调整方案随时告诉我。"))
            return

        # confirmed 流程推进（按已发生的工具调用）
        if n_confirm_cards == 1 and n_search == 0:
            print("  [mock] R2 → 真实执行 web_search（MOF-5 合成价格）", flush=True)
            self._respond(tc("web_search", {"query": "MOF-5 metal-organic framework synthesis cost price", "max_results": 3}, "call_search"))
            return
        if n_confirm_cards == 1 and n_read == 0:
            # R3 → 真实执行 read_file（无副作用、本地快）——验证"确认后
            # 真实工具执行 → 回合继续 → 第二张卡"完整链路（127 根因已定位：
            # turn_runner 循环无 bug——后端复现 PASS；此前卡住是 E2E 审批
            # 弹窗环境差异。恢复真实工具覆盖真链路）
            print("  [mock] R3 → 真实执行 read_file（校验生成物）", flush=True)
            self._respond(tc("read_file", {"path": "README.md"}, "call_write"))
            return
        if n_confirm_cards == 1:
            print("  [mock] R4 → 上传确认卡", flush=True)
            self._respond(tc("ask_user_confirm_card", {
                "title": UPLOAD_TITLE, "message": UPLOAD_MESSAGE,
                "choices": UPLOAD_CHOICES, "timeout_seconds": 60,
            }, "call_upload_confirm"))
            return

        print("  [mock] R5 → 上传确认收到 confirmed，输出最终结果", flush=True)
        self._respond(text(
            "✅ 已完成并上传：\n"
            "- 工作流：MOF-5 市场合成价格报告（4 节点）\n"
            "- 文件：mof-price-report.workflow.json（已生成在工作区）\n"
            "- 校验：WorkflowSpec v1.0.0 通过\n"
            "- 项目入口：forge.miqroera.com/projects/mof-price-report"
        ))


if __name__ == "__main__":
    # Optional port argument: E2E passes an ephemeral port so parallel
    # workers / CI retries never collide on 8899. Port 0 lets the OS pick.
    import sys

    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8899
    server = HTTPServer(("127.0.0.1", port), Handler)
    # Print the ACTUAL bound port — with port 0 it differs from argv.
    actual_port = server.server_address[1]
    print(f"Mock OpenAI server on http://127.0.0.1:{actual_port}/v1", flush=True)
    server.serve_forever()
