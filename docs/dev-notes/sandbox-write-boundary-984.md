---
name: sandbox-write-boundary-984
description: #984 沙箱写边界（层 1+2+3）—— /mnt 只读 + per-call rw bind + 静态词表，边界与残余缺口清单
type: project
---

2026-09-09，#984（`fix/984-sandbox-write-boundary`，PR #1007）落地三层写边界。
本文记录**边界在哪、缺口在哪**，避免后续把「护栏」当成「完备」。

## 三层各是什么

| 层 | 位置 | 性质 | 作用 |
|---|---|---|---|
| 层 2 | `miqi/sandbox/bwrap.py` `--ro-bind-try /mnt /mnt` | **内核强制** | 整个 Windows 用户数据区不可写；任意拼写（`open(...,'w')`、`write_text`、`shutil.copy`）都被 EROFS 挡住 |
| 层 1 | per-call `extra_rw_binds`（硬 `--bind`） | **内核授权** | 把用户点名的输出目录 / 工作区 / 静态根重新打开为可写 |
| 层 3 | `miqi/agent/command_guard.py` 词表 | **体验层（可绕过）** | 更早、更可读的报错 + 安全替代提示；**不是**强制层 |

一句话取舍：**强制靠挂载（层 1/2），层 3 只负责把「注定失败」的命令提前讲清楚。**
静态分析不可能完备——词表没命中的写法一律 fail-open（放行，由层 2 兜底），
绝不为了「更严」而误拦合法写入。

## 层 1/2：内核强制与内核授权

`bwrap` 挂载是唯一的内核强制点。本 PR 把它收紧：

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

## 层 3：静态词表（体验层，可绕过）

`command_guard.py` 的内联脚本词表（`_DESTRUCTIVE_CALL_RE` + `_open_call_writes`）
补齐 python 写系拼写：

- **写**：`open(..., 'w'/'a'/'x'/'wb'/'ab'/'r+'/'wt')`（含 `X.open('w')` 方法形式与
  `mode='w'` 关键字）、`write_text` / `write_bytes`、`os.mkdir` / `os.makedirs` /
  `Path.mkdir`、`shutil.copy*`、`shutil.move`、`os.rename` / `os.replace`。
- **读不误报**：`open(f)` / `open(f,'r')` / `open(f,'rb')` 一律不触发。模式串只从
  该次调用的**参数表**里取（括号感知），所以 `open(os.path.join(d,'a'),'r')` 仍算读。
- **`shutil.copy*` 源侧按读处理**：与 shell `cp /etc/x out` 对齐——源可读、目标必须在
  范围内。`shutil.move` / `os.rename` 会删源，两侧都算变更。
- **heredoc 正文保真**：正文从**原始文本**读（`_raw_heredoc_body`），不再用 tokenizer
  拼接——拼接会吃掉引号，路径字面量全丢 → 一律 `script_uncertain`，授权目录的
  `python3 - <<EOF` 交付会被误拦。
- **授权目录不误拦**：`RuntimePaths.extra_write_roots` 承接 per-call `_user_roots`
  （`tools.auto_user_dirs` 开时才生效，**与层 1 的 bind 同一开关**），授权子树内的
  写/改名/移动/删一律放行；系统路径仍优先拒绝（纵深防御）。

**显式授权动作（#821）**：用户消息里点名目录（`C:\Users\<u>\Desktop\<dir>`、
`/mnt/c/...`、POSIX 绝对路径）→ `extract_user_mentioned_roots` 抽取 → harness 每轮
**无条件**注入 `_user_roots`（模型自带副本一律剥除）→ 层 1 硬 `--bind` 开写、层 3 同步
放行。没点名 = 没授权 = 层 2 EROFS + 层 3 拒绝。

**静态根（`tools.extra_roots`）不一样**：它进层 1 的 bind 集合、也进文件工具的合法根，
但**不进层 3 的 `extra_write_roots`**——exec 的内联写（shell 与 python 同口径）仍按会话
范围判定，写这类目录会拿到「请用文件工具 / 让用户点名」的拒绝。原因是静态根里含工作区
根，整包纳入会破坏 Level 1 与跨 session 保护（工作区根不能变成可写区）。`python3 script.py`
这类**无内联 payload** 的写法不受影响（层 3 看不见，由层 1 的 bind 决定）。

## 已知缺口（不覆盖）

1. **层 3 只是体验层，词表必然不完备**：没进词表的写法（`numpy.save`、`os.open`、
  `pandas.to_csv`、`subprocess.run(['cp', ...])`、拼字符串动态调用 …）一律 fail-open
  放行，交给层 2。**已知绕过面**：
   - **宿主回退**：沙箱未就绪 / `get_or_create` 返 None / `tools.sandbox.enabled=false`
     / 非 WSL → 退回宿主执行，此时**只剩层 3**（静态词表 + legacy deny patterns）。
   - **非 bwrap 路由**：安装路由（root 跑在 WSL、不经 bwrap）、documents 工具
     （`pdf_create_tool.py` 宿主写）、MCP 写文件、graph_render 宿主分支。
   - **KUN exec**：`kun_runtime/tool_host.py` 的 `_USER_ROOTS_TOOLS` 不含 `exec`，
     且只对白名单工具注入 `_user_roots` → 层 2 之后 KUN 链的 exec 写用户提及目录
     会失效（KUN 未接入主执行路径，政策见 [legacy-main-path-only](legacy-main-path-only.md)）。
   - **沙箱内路径写**：`/home/miqi/**`、`/tmp` 是沙箱 overlay，写不落宿主；但自定义
     工作区模式把宿主工作区 bind 到 `/home/miqi/workspace`，那一部分**就是**宿主路径
     （本身即会话自己的可写区）。
2. **读 + 外传不受限**：`share_net=True`，`/mnt` 只读不影响读。
3. **仅卡片授权（#864）的目录**：文件工具可写、exec 在 ro `/mnt` 下 EROFS（授权存在
   文件工具实例 `self._granted`，ExecTool 无引用）。
4. **提及但未创建**的目录：exec 的硬 `--bind` 直接失败（ExecTool 产出引导文案：
   先用文件工具写一次）；文件工具会**宿主侧 mkdir bootstrap**，所以顺序敏感——
   先文件工具、后 exec。
5. **子 agent 重启丢根，且 `agent.spawn` 不再从请求里取根**：

   - **只为内存**：`AgentJob.user_roots`（`agent_jobs.py:39`）不落库——
     `AgentGraphStore` schema 固定、`save_job` 只收显式关键字。子 agent
     重启/重载后丢根，这一点**仍然成立**。
   - **请求参数不是授权**：AppServer `agent.spawn`（`bridge/loop.py:1508`）
     自 `a17d2812` 起固定传 `user_roots=None`（fail-closed），原先那句
     "re-filtered here" 的 sanitize 已随该提交删除。理由是 roots 同时喂给
     bwrap 的 rw bind 与命令护栏的写范围，而 `params` 由调用方自带——
     把请求里的列表「过滤一遍」也只是让调用方继续挑范围，所以宁可**不给根**：
     该通道**没有服务端 root store**（父回合的授权根没按 session/turn 记录），
     等服务端状态接入后再注入。
   - **该 IPC 路径当前不可达**：Desktop `AgentSpawnInput`
     （`apps/desktop/src/shared/ipc.ts:299`）不含 `user_roots`，
     `apps/desktop/src/main/ipc/index.ts:2164` 在 Zod parse 之后只转发
     `agent_type`/`task`/`label`/`session_key`。
     **当前可达的路径**是模型侧 `SpawnTool._user_roots`
     （`miqi/agent/tools/spawn.py:88`）→ `AgentControl.spawn` →
     `AgentJobRuntime.start` → `AgentJob.user_roots`：roots 走的是
     **harness-only 的 `_user_roots`/extra 通道**（`ToolRegistry` 会把模型自带
     参数里的 `_user_roots` 剥掉）。

## 顺手修掉的 bug

- `_sandbox_write_file` 旧写法 `mkdir -p '$(dirname "…")'`：**单引号内不做命令
  替换**，bash 建出字面目录 `$(dirname "…")`，重定向 rc=1——凡是写新子目录必挂。
  修法是**在 Python 里算 dirname**；**不要**把外层引号改双引号，否则 Windows
  目录名里的 `$`/反引号会变成命令替换。
- `extract_user_mentioned_roots` 的前缀表：`_TOP_LEVEL_SYSTEM_DIRS` 只挡 depth-1，
  `C:\Windows\Temp\x` / `/etc/cron.d/x` 之前会变成可写根。注意
  `users`/`home`/`mnt` **必须**留在表外——`C:\Users\<u>\Desktop\<dir>` 正是主场景。

**How to apply:** 改动沙箱写路径时先问「这条命令最终走 bwrap 还是宿主」。走 bwrap
才有层 1+2；走宿主就只有静态护栏（层 3）。给写入口加新路径时，记得同时接上
`extra_rw_binds`（bind 集合）与 `bootstrap_sandbox_roots`（目录必须存在）；给
`command_guard.py` 加词表条目时，先想清楚「授权目录里的同一条命令会不会被它误拦」
——授权通道（`_user_roots` → `extra_write_roots`）必须与词表同进同退。
