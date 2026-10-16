---
name: sandbox-and-workspace-switch-e2e-facts
description: 工作目录切换会新建会话、sandbox 在创建时读「已落盘」workspace；ubuntu runner 无法 unshare-user 故 sandbox 用例须确定性 skip
type: project
---


2026-09-16（PR #1115，修 3 个 flaky E2E）实测得到的产品/环境事实，写 E2E 时直接可用：

**1. 「更换工作目录 → 浏览」会新建会话，不是改当前会话**
`onBrowse → createSession('desktop:'+Date.now()) → App.handleSessionCreated`，新 key + `pendingWorkspace`；渲染层随后用 `sessions.get(key,{workspace})` 把绑定**落盘**，而胶囊（`inline-workspace-path`）是乐观显示。所以：切目录后要用「当前 key」（`localStorage['miqi:lastSession']`）而不是切之前的 key 去等状态。

**2. sandbox 的工作区绑定在「创建时」从会话元数据读**
`SandboxManager.get_or_create → _resolve_session_workspace → session.metadata['workspace']`（磁盘）。首个 exec 早于绑定落盘 → 挂载默认的 per-session 私有副本（空）→ `ls` 合法地无输出。这正是 `workspace-file-read-edit-sandbox` 失败的真相之一（另一条是 runner 跑不了 bwrap）。

**3. ubuntu-latest(24.04) 跑不了 bwrap 的非特权 user namespace**
`bwrap --unshare-user` → `setting up uid map: Permission denied`（AppArmor 限制），但 `runtime.status().sandbox_available` 仍为 true（初始化只探二进制存在）。因此 Linux 上跑 sandbox 用例要先做 host 探针（与 `_build_bwrap_args` 同构的 `--unshare-pid --unshare-user --uid … --gid …`），跑不了就确定性 skip；靠模型措辞猜环境一定会 flaky（同一台坏 runner 一次答「（无文件输出）」一次照抄 bwrap 报错）。

**4. 「exec 到底跑没跑」的硬信号**
`[data-testid="tool-command-copy"]`（工具命令行）在**本回合**新增即证明 exec 被调用；模型措辞不可靠。exec stdout/stderr 走 `miqi.chat.onProgress` 的 `stream/delta`（legacy 适配层 `turn_event_adapter` 映射 `ExecCommandOutputDeltaEvent`），失败时可能一个 delta 都不发。

**5. 轮询上限必须严格小于测试超时**
`RUN_CAP == test timeout` 时循环永远来不及自己退出，报错只剩 `Test timeout of … exceeded`（栈落在 `page.waitForTimeout`），后面的断言/precondition 跳过全执行不到。另外空闲判定别用 `main.textContent` 原文比较：「已深度思考 · N 秒」计时器每秒改写字符，会把 idle 阈值无限往后推（改比对抹掉 `· N 秒` 后的文本）。

相关：[local-real-config-no-provider](local-real-config-no-provider.md)、[macos-ci-local-mock-server](macos-ci-local-mock-server.md)、[e2e-localstorage-shared-userdata](e2e-localstorage-shared-userdata.md)、[playwright-describe-skip-kills-timeout-option](playwright-describe-skip-kills-timeout-option.md)
