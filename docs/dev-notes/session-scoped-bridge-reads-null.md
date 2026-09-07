---
name: session-scoped-bridge-reads-null
description: E2E/桥接下裸名+session_key 的 documents.parse/files.read 返回 null，必须用 sessions/<safeKey>/files/<name> 全路径且不带 session key
type: project
---

E2E 环境（真实桥接）下，`documents.parse(name, sessionKey)` 与 `files.read(name, sessionKey)` 对裸名返回 null（桥层吞掉错误，不抛异常）；裸名不带 session key 也 NOT_FOUND。**唯一可靠形式**：`sessions/<safeKey>/files/<name>` 全路径 + 不带 session key（workspace 作用域解析）。ChatConsole 的 HTML 预览分支注释已记载此坑，#877 富预览沿用同一策略（候选列表加 fullRel 项）。

**Why:** 首次在 proof-751 之后编写 #877 E2E 时用裸名+session key 调用，3 个 spec 全部掉进 openExternal 兜底，probe spec 逐条对比才定位。解析耗时也极长——Windows 上后端首次 openpyxl import 约 28s，首个断言要给 45s 超时。

**How to apply:** 写 E2E 时手动 stage 文件到 `sessions/<safe>/files/` 后，断言超时放宽（45s）；前端代码里任何 bridge 读/解析优先尝试 fullRel 无 session key 形式。相关：[confirm-card-two-runtime-map](confirm-card-two-runtime-map.md)、[e2e-exec-slow-spawn-timeout](e2e-exec-slow-spawn-timeout.md)
