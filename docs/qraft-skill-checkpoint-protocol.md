# qraft-workflowspec-export Skill 上传流程协议（#674）

> 状态：**设计定稿**（2026-08-13，ChatGPT 架构评审 + 双 AI 设计评审后定稿）
> 实施状态：Skill 本体待接入（平台组信息 Blocked）——本协议是 Skill 侧实现的规格依据
> 决策依据：ChatGPT 架构评审定稿（#674/#671 双 AI 设计评审结论，见 issue 讨论记录与
> `docs/qraft-skill-checkpoint-protocol.md` 正文；本地存档 `issue-674-671-架构决策存档.md`
> 仅作作者工作笔记，不作为仓库依赖）

---

## 1. 核心架构：Checkpoint Protocol（两阶段 + 可恢复事务）

**原则**：Skill 脚本**不是等待用户的主体**。一次上传 = 可暂停/可恢复事务：

```
             Skill Phase 1（export + validate + prepare）
                  │
                  ▼
        confirmation checkpoint 写入
                  │
             process exits（进程可结束，事务不丢）
                  │
                  ▼
        Agent 读取 checkpoint
                  │
                  ▼
       ask_user_confirm_card（模型弹卡，绑定 run_id + sha256）
                  │
       ┌──────────┼───────────┐
       │          │           │
     confirm    cancel      modify
       │          │           │
       ▼          ▼           ▼
 Skill resume   terminal   Agent 重新规划方案
       │                      │
       └───────────┬──────────┘
                   ▼
             Upload Phase（校验 sha256 未变）
                   │
                   ▼
                 Done
```

**模型角色**：orchestration layer（读 checkpoint → 弹卡 → 按 decision 调 Skill），
**不是** transaction layer（不搬运 JSON、不改 sha256、不重组校验报告）。

## 2. Checkpoint 文件格式（miqi-skill-checkpoint/v1）

```json
{
  "protocol": "miqi-skill-checkpoint/v1",
  "run_id": "uuid",
  "skill": "qraft-workflowspec-export",
  "action": "confirm_upload",

  "artifact": {
    "path": "workflow_runs/abc/workflowspec.run.<日期>.json",
    "sha256": "deadbeef...",
    "size_bytes": 18342
  },

  "artifact_pair": {
    "definition": "workflow_runs/abc/workflowspec.definition.<日期>.json",
    "run": "workflow_runs/abc/workflowspec.run.<日期>.json"
  },

  "validation": {
    "status": "VALID",
    "errors": [],
    "warnings": [
      { "code": "CLAIM_MISSING_EVIDENCE", "severity": "B", "path": "$.claims[2]", "message": "2 个数据点缺少来源引用", "hint": "补充引用或标注推断" }
    ]
  },

  "summary": {
    "title": "...",
    "steps": 7,
    "artifacts": 4,
    "claims": 6
  },

  "confirmation": {
    "required": true,
    "choices": ["confirm", "modify", "cancel"]
  },

  "created_at": "...",
  "expires_at": "..."  # 可选：过期后 checkpoint 视为失效——不弹「过期确认」，
                        # 直接提示重跑 Phase 1（#686-1 审阅：过期策略）
}
```

**关键字段**：`run_id` + `artifact.sha256`——确认绑定用。
`resume` 时重新校验文件 sha256 == checkpoint 的 sha256，不一致 →
`ARTIFACT_CHANGED_AFTER_CONFIRMATION` 禁止上传（防"确认 A 上传 B" TOCTOU）。

**sha256 一致性（2026-08-14 skill 同步教训）**：算指纹与写文件必须用
**同一组序列化参数**（ensure_ascii / 换行符）——曾因「算指纹不转义中文、写文件转义
+ Windows CRLF」导致 definition_sha256 永远失配。checkpoint 的 sha256 必须
对**磁盘上实际存在的文件**计算（`hashlib` 读字节流，不做任何文本规范化）。

**产物规范（skill 变更后）**：只两份正式 JSON——`workflowspec.definition.<日期>.json`
（菜谱）+ `workflowspec.run.<日期>.json`（记账本），**无 bundle**（官方 schema 只认
两种文档，bundle 永远校验不过）。生成：`scripts/build_documents.py`（已从
build_bundle.py 改名）。项目信息（UUIDv5 幂等 / 标题 / 描述）在
`run.extensions.x-project-*`；技术路线在 `definition.graph`。上传目标 = run 文档；
checkpoint 的 `artifact_pair` 同时登记两份（definition 只读校验，run 上传）。

## 3. 三态决策

| 决策 | 行为 |
|---|---|
| **CONFIRM** | Agent 调 Skill resume（重新校验 sha256）→ 上传 |
| **CANCEL** | 终止事务（checkpoint 标记 done） |
| **MODIFY** | **Skill 不改 JSON**——Agent 重新规划方案 → 重新 export → 重新 validate → 新 checkpoint → 重新确认（Plan v2 循环）。**上界：连续 3 次 MODIFY 后（#686-2 审阅）**——停止循环，向用户明示「方案经 3 次修改仍未通过校验，是否仍要上传（风险自负）」二选一：强制上传或终止任务 |

## 4. 目录约定

```
workspace/.miqi/skills/qraft-workflowspec-export/runs/<run_id>/
  ├── workflow.json          # 导出产物
  ├── proposal.md            # Markdown 方案视图
  ├── validation_report.json # JSON（canonical source）
  ├── validation_report.md   # 人类可读版
  └── checkpoint.json        # 确认 checkpoint
```

- 业务状态 → workspace（任务上下文，会话结束可清理）
- **token 单独存放** → `~/.miqi/credentials/qraft/token.json`（用户级凭据，权限 0600/用户 ACL）

## 5. validation_report 格式

```json
{
  "status": "INVALID | VALID",
  "errors": [{"code": "MISSING_DOCUMENT_KIND", "severity": "A", "path": "$.document_kind", "message": "...", "hint": "..."}],
  "warnings": [{"code": "CLAIM_MISSING_EVIDENCE", "severity": "B", "path": "$.claims[2]", "message": "...", "hint": "..."}]
}
```

| 级别 | 行为 |
|---|---|
| A 级错误 | **禁止上传**（必拦） |
| B 级警告 | 允许上传，但**必须上确认卡明示**（ask_user_confirm_card `warnings` 字段，已入契约） |
| INFO | 正常 |

## 6. OAuth2 token 管理（lazy refresh + double-check lock）

```python
# 上传前
with token_lock:                      # 文件锁
    token = load("~/.miqi/credentials/qraft/token.json")
    if token.expires_at - now > 5min: # safety_window = 5min
        return token                  # 别人刷好了直接用
    new = refresh(token.refresh_token)
    save(new)
    return new
```

- 保存 `expires_at`，lazy refresh（无守护进程）
- **expires_at 防御（#686-4 审阅）**：`expires_at` 缺失/非数值/已过期 → 视为**不可用 token**，
  不尝试 refresh，直接要求用户重新授权（`REAUTH_REQUIRED` 提示）
- **401 = credential recovery path**：refresh once + retry once，绝不无限循环
- **平台组答复前的 401 降级（#686-4）**：refresh_token 语义未确认前，若 refresh 失败
  （invalid_grant / 网络错误）→ **不自动重试**，返回 `REAUTH_REQUIRED`——提示用户
  「Qraft 登录已过期，请重新授权后再继续上传」
- **无幂等时不盲目自动重试**（ChatGPT 最终评审 #10）：重试只分两类
  - definite-safe retry：连接建立前明确失败（平台未收到请求）→ 可重试（0.5/1/2s 退避 + jitter，max 3）
  - ambiguous（POST 已发出、响应丢失/timeout）→ **UPLOAD_STATUS_UNKNOWN**——提示用户「请求可能已提交，请前往 Qraft 确认」，绝不自动重发
- **幂等性待平台组确认**（dataUpload 是否支持 request_id/idempotency_key / GET status(run_id)）——确认前维持上述保守策略
- run_id 贯穿日志（`[run=ab12] upload attempt=1`）；敏感信息脱敏（secret_mask）

## 7. 与 MiQi 契约的对接（已完成）

`ask_user_confirm_card` 已支持（见 #684）：
- `warnings`: B 级校验警告上卡明示
- `metadata`: {run_id, artifact_name, artifact_size, artifact_sha256} 确认绑定展示

Skill resume 的触发：Agent（模型）在确认卡结果为 confirmed 后，
重新调用 Skill 的上传命令（带 run_id 参数）——由模型编排，Skill 无状态恢复。

## 8. 待平台组确认（Blocked 项）

1. dataUpload 幂等性（request_id 支持？）
2. refresh_token 真实语义（expiry/revoke/轮换——"不轮换 ≠ 永久有效"）
3. OAuth2 测试环境凭据
4. 出口 IP 白名单（403 处理）
