---
name: sandbox-write-boundary-984
description: #984 沙箱写边界 PR1 —— /mnt 改只读后，哪些写入口受内核约束、哪些仍有缺口
type: project
---

2026-09-09，#984 PR1（`fix/984-sandbox-write-boundary`）落地三层写边界。
本文记录**边界在哪、缺口在哪**，避免后续把「护栏」当成「完备」。

## 强制层是什么

`bwrap` 挂载是唯一的内核强制点。PR1 把它收紧：

- **层 2**：`--bind-try /mnt /mnt` → `--ro-bind-try /mnt /mnt`
  （`miqi/sandbox/bwrap.py` `_build_bwrap_args`）。整个 Windows 用户数据区
  不再可写，`python -c "open(...,'w')"` 之类任意拼写都被 EROFS 挡住。
- **层 1**：per-call `extra_rw_binds` 用**硬 `--bind`** 把授权子树重新打开，
  排在 `/mnt` ro 之后（bwrap 后挂载覆盖前者）。**禁 `--bind-try`**：源不存在
  必须大声失败，不能静默丢掉刚授予的写权限，更不能回退宿主执行。

bind 集合：

```
exec 侧  = 工作区根 ∪ 静态 _shared_roots ∪ (auto_user_dirs ? _user_roots : ∅)
文件工具 = 工作区根 ∪ 静态 _shared_roots ∪ (allow_user_roots ? _user_roots : ∅) ∪ #864 已授权(shared)
```

- exec 的四个 `_execute_*` 签名都接受 `extra_rw_binds`，8 处 `**splat` 全打通；
  宿主执行分支收下即忽略（宿主没有 mount namespace，忽略**不是**降级）。
- 文件工具四个写入方（`write_file` / `edit_file` / `apply_patch` /
  `graph_render`）各自把 `shared` 传给 `_sandbox_write_file`。
- **#864 卡片授权不进 exec 集合**：授权存在文件工具实例的 `self._granted`，
  ExecTool 没有它的引用。仅卡片授权的目录 → 文件工具可写、exec 在 ro `/mnt`
  下 EROFS。

## 已知缺口（PR1 不覆盖）

1. **层 3 未做**：`command_guard.py` 的 python 写系词表（`write_text` /
   `open(...,'w')` / `shutil.copy*` …）留给 PR2。它是**体验层**（更早、更可读的
   报错），不是强制层——强制层是上面的挂载。
2. **宿主回退不受约束**：沙箱未就绪 / `get_or_create` 返 None /
   `tools.sandbox.enabled=false` / 非 WSL，全部退回宿主执行，只剩静态护栏。
3. **非沙箱写入方不受约束**：documents 工具（`pdf_create_tool.py` 宿主写）、
   MCP、graph_render 的宿主分支、安装路由（root 跑在 WSL、不经 bwrap）。
4. **读 + 外传不受限**：`share_net=True`，`/mnt` 只读不影响读。
5. **KUN 链**：`kun_runtime/tool_host.py` 的 `_USER_ROOTS_TOOLS` 不含 `exec`，
   且只对白名单工具注入 `_user_roots` → 层 2 之后 KUN 链的 exec 写用户提及目录
   会失效（KUN 未接入主执行路径，政策见 [legacy-main-path-only](legacy-main-path-only.md)）。
6. **提及但未创建**的目录：exec 的硬 `--bind` 直接失败（ExecTool 产出引导文案：
   先用文件工具写一次）；文件工具会**宿主侧 mkdir bootstrap**，所以顺序敏感——
   先文件工具、后 exec。
7. **子 agent 重启丢根**：`AgentJob.user_roots` 只在内存（`AgentGraphStore`
   schema 固定，不持久化）。AppServer `agent.spawn` 由 Desktop 传
   `params["user_roots"]`，不发则子 agent 无根。

## 顺手修掉的 bug

- `_sandbox_write_file` 旧写法 `mkdir -p '$(dirname "…")'`：**单引号内不做命令
  替换**，bash 建出字面目录 `$(dirname "…")`，重定向 rc=1——凡是写新子目录必挂。
  修法是**在 Python 里算 dirname**；**不要**把外层引号改双引号，否则 Windows
  目录名里的 `$`/反引号会变成命令替换。
- `extract_user_mentioned_roots` 的前缀表：`_TOP_LEVEL_SYSTEM_DIRS` 只挡 depth-1，
  `C:\Windows\Temp\x` / `/etc/cron.d/x` 之前会变成可写根。注意
  `users`/`home`/`mnt` **必须**留在表外——`C:\Users\<u>\Desktop\<dir>` 正是主场景。

**How to apply:** 改动沙箱写路径时先问「这条命令最终走 bwrap 还是宿主」。走 bwrap
才有层 1+2；走宿主就只有静态护栏。给写入口加新路径时，记得同时接上
`extra_rw_binds`（bind 集合）与 `bootstrap_sandbox_roots`（目录必须存在）。
