# 零 CI 覆盖清单（#1196）

本文件是 `apps/desktop/tests/e2e/*.spec.ts` 中**至少有一个用例在任何 CI runner 上都不会执行**
的权威清单。口径是「用例」而不是「文件」：标了（部分）的行（`full-electron`、`task-assets`）
其余用例在 CI 上照跑，只有被守卫挡住的那些从不运行、也从不报错——正是「e2e 覆盖率」最容易
高估的部分（#1196）。

**CI 上真正的执行位置只有三处**：

| 位置 | 平台 | 范围 |
|---|---|---|
| `desktop-ci.yml` → `electron-e2e` | ubuntu | 全量；provider key 写进 app 的 **config.json**（不是环境变量），环境变量注入 `QRAFT_PHONE` / `QRAFT_PASSWORD` / `QRAFT_LIVE=1` / `QRAFT_PHONE_A`·`QRAFT_PASSWORD_A`·`QRAFT_PHONE_B`·`QRAFT_PASSWORD_B`（后五个 2026-10-15 随「点亮 live 用例」补上；A/B 供 #1185 双账号用例，账号 secret 缺席时它们仍如实 skip）；**不含**任何 `MIQI_RUN_*` |
| `desktop-ci.yml` → `macos-e2e` | macOS | `--grep-invert` 掉 9 组之后的子集（见 README 覆盖表）；连 `QRAFT_PHONE` / `QRAFT_PASSWORD` 都不注入 |
| `python-tests.yml` → `wsl-e2e` | windows | **按名字**点名 7 个：one-click-install、sandbox-exec、session-key-mapping、sandbox-toggle、#1157、#1171、open-external-path-security |
| `sure-e2e.yml` → `sure-e2e` | ubuntu | **按名字**点名 2 个：`acceptance-page`、`sure-event-bridge`；**CI 内构建 fork 版 SURE**（钉死 sha，构建后断言 `doctor` 有 miqi 行，没有即红）；注入 `SURE_OPENAI_API_KEY`（=`DEEPSEEK_API_KEY`）、`MIQI_E2E_FULL_REPAIR=1` 与 `QRAFT_LIVE=1` + `QRAFT_PHONE`/`QRAFT_PASSWORD`（acceptance 的真实登录段，2026-10-15）；跑完校验 `test-reports/results.json`——两条 spec 内出现 skipped 即判失败（`SURE_E2E_SKIP_ALLOWLIST` 默认空 = 无豁免） |

注意「环境变量」与「config.json」的区别：spec 的守卫读的是 `process.env`。app 能从 config.json
拿到 provider key，不代表 spec 的门也成立——`billing-hosted-live.spec.ts` 把 `DEEPSEEK_API_KEY`
写进了 `HAS_CREDS`，而该变量从未作为环境变量注入任何跑 e2e 的 job，于是它永远跳过（见下表）。

所以「Linux job 是全量」这句话对下表里的 spec 不成立。判罚与维护方式：
`apps/desktop/tests/e2eCiCoverage.test.ts`（`npm test`，quick job）会校验本文件——
**检测到致命守卫却没登记 → 红；登记为「守卫」的行守卫已消失 → 红**。

检测是 job/step 粒度的：测试先解析每个 workflow 的 job（`runs-on`、各级 `env`）与 step
（哪个 step 按名字收集了哪些 spec、哪个 step 全量跑 electron 项目），只有**真正会收集到
这个 spec 的 job/step** 里设置过的变量才算「CI 已设置」。所以 `billing-hosted-live` 那种
「变量在别的 job / config.json 里出现过、收集它的 job 却没注入」的情况会被如实判为致命门。
第二列「判定」：`守卫` = 由上述测试自动检测；`人工` = 守卫形态检测不到、但人工确认过零覆盖
（现存 2 行：#1185 双账号用例——env 接线已就位、账号 secret 未配，缺值时如实 skip）。

| spec | 判定 | 门 | 运行代价 | 为什么不在 CI 跑 |
|---|---|---|---|---|
| `approval-persistence.spec.ts` | 守卫 | `const SKIP_APPROVAL_ON_CI = !!process.env.CI` | 低（无 LLM） | CI 的 config 里 `commandApproval.enabled=false`，确认弹窗根本不出现——测的就是弹窗持久化，没有可测对象 |
| `issue-821-user-output-dir.spec.ts` | 守卫 | `SKIP_SANDBOX_E2E`（`MIQI_RUN_SANDBOX_E2E=1` 只在沙箱点名步骤里设置）+ win32 | 高（真实 LLM + WSL 沙箱） | 回归的是 WSL 合法根检查；spec 头注明 Python 单测（`test_user_roots.py` 等）是主验证，本 E2E 是补充 |
| `mof5-qraft-upload.spec.ts` | 守卫 | win32 + 真实 `<workspace>/.qraft/token.json` | 极高（真实 LLM，单用例上限 45 分钟） | 需要本机真实 Qraft 登录 + MOF-5 工作区；CI 既无凭据也无工作区，**CI 不可能跑** |
| `no-git-bash-cmd-fallback.spec.ts` | 守卫 | win32（cmd `%RANDOM%` 标记） | 中（真实 LLM） | 断言的是 cmd.exe 的 `%RANDOM%` 输出语义，只有 Windows 成立 |
| `wsl-inplace-file-write.spec.ts` | 守卫 | `SKIP_SANDBOX_E2E` + win32 | 高（真实 LLM + WSL 沙箱） | 测 WSL 沙箱内就地写文件，Windows + WSL 独有；该文件第 3 个用例是手动验证占位（体内无条件 `test.skip()`），本地也不跑 |
| `global-prompt-skill-rule.spec.ts` | 守卫 | `MIQI_RUN_REAL_SKILL_E2E !== '1'` | 中（真实 LLM） | 真实模型的技能选择不稳定，留本地手动 / 夜间验证（spec 头即如此注明） |
| `pdf-generator.spec.ts` | 守卫 | `MIQI_RUN_REAL_PDF_E2E !== '1'` | 低（文件里只有一个占位用例） | 唯一用例是「手动验证占位」，体内无条件 `test.skip()`——本地也不跑；主验证在 Python 单测 `tests/documents/`，门只是防止将来补上真用例时在 CI 上失控 |
| `pptx-generator.spec.ts` | 守卫 | `MIQI_RUN_REAL_PPTX_E2E !== '1'` | 中（真实 LLM） | 同上；macOS job 还额外 `--grep-invert` 掉了 "PPTX Generator" |
| `skill-invocation-eval.spec.ts` | 守卫 | `MIQI_RUN_SKILL_EVAL !== '1'` | 高（多轮真实 LLM 的量化评估） | 评估型用例：本地默认跑、CI 明确关闭（spec 头注明） |
| `issue-1034-renderer-oom-probe.spec.ts` | 守卫 | `MIQI_1034_PROBE === '1'` | 极高（长跑测量） | spec 自身注明「测量用长跑探针默认跳过」，只在排查 #1034 时手动开 |
| `issue-1036-rpc-starvation-probe.spec.ts` | 守卫 | `MIQI_1036_PROBE === '1'` | 极高（真实模型 + 真实 exec 长跑；窗口默认 20 分钟） | 测量用长跑探针：默认跳过，只在排查 #1036「长 turn 期间部分 RPC 被饿死」时手动开。判据是「一次 `config.get` settle 用时 ≥ 30s」即命中（30s 是判据阈值；720s 是它的客户端超时上限，不是命中所需时长），窗口必须同时长到能容纳若干采样，electron-e2e 的 job 上限撑不住；它回答的是「这个问题是否存在」而非回归 |
| `full-electron.spec.ts`（部分） | 守卫 | `MIQI_RUN_REAL_WEB_SEARCH_E2E` / `MIQI_RUN_STATEFUL_SESSION_E2E` | 高（真实 LLM） | 5 个用例在 PR CI 上不稳定（#187）：real web search 1、stateful session 1、sidebar switching 2、重启 history 1；该文件其余 11 个用例在 Linux 全量套件里照跑 |
| `issue-1185-account-isolation-live.spec.ts` | 人工 | `QRAFT_LIVE=1` + ≥2 个真实账号 | 中（真实平台账号） | electron-e2e 的 env 接线 2026-10-15 已就位（`QRAFT_PHONE_A/B` 等），但仓库目前只有一对账号 secret——缺值时 spec 如实 skip，静态检测已看不见致命门，故人工登记。补齐 A/B（可选 C）账号 secret 即自动点亮；需要两个账号来回切换验证本地存储隔离 |
| `issue-1185-task-assets-live.spec.ts` | 人工 | `QRAFT_LIVE=1` + 真实账号 A/B | 中（真实平台账号） | 同上：复杂技能产物跨账号切换；与 isolation 一起等 A/B 账号 secret 补齐后点亮 |
| `billing-live.spec.ts` | 守卫 | `SLURM_MCP_KEY` | 中（真实网关） | 需要 slurm MCP 网关 key，CI 未注入 |
| `billing-hosted-live.spec.ts` | 守卫 | `HAS_CREDS` 里的 `DEEPSEEK_API_KEY` | 中（真实账号 + 真实计费网关） | 该变量只被写进 config.json、从未作为环境变量注入任何会收集本文件的 job（`electron-e2e` / `macos-e2e` 只注入 `QRAFT_PHONE` / `QRAFT_PASSWORD`），`HAS_CREDS` 恒为 false。要让它在 CI 真跑，需在 `electron-e2e` 注入该变量（live 用例是否每 PR 都花真实额度是产品决策） |
| `mof-synthesis-price-agent.spec.ts` | 守卫 | `MOF_PRICE_PROJECT`（含 extract/、enrich/、report.py 的项目根）+ 私有技能目录 | 高（真实 LLM） | 项目根与 `miqi/skills/mof-synthesis-price-agent` 都是本机私有资产，仓库里没有 |
| `bvse-skill-assets.spec.ts` | 守卫 | `BVSE_SKILL_DIR`（默认 `~/.forge/skills/bvse-mof-local-ssh`，需含 `.venv`）+ `BVSE_TEST_CIF`（真实 MOF CIF 文件） | 中（本机技能 venv + mock server，长流程） | 需要本机装好的 BVSE 技能依赖与真实 CIF 文件；macOS CI 还因 loopback 到 mock server 不可达额外跳过 |
| `record-bvse-skill.spec.ts` | 守卫 | 同 `bvse-skill-assets`（`RECORD_OUT_DIR` 只是可选输出目录，不是门） | 中（同左，且全程录屏） | 同上：录屏演示真实 BVSE 技能，依赖本机环境 |
| `tool-error-neutral.spec.ts`（部分） | 守卫 | `SKIP_SANDBOX_ON_CI`（`MIQI_RUN_SANDBOX_E2E`） | 中（真实 LLM + WSL 沙箱） | 该变量只在 wsl-e2e 的沙箱点名步骤里设置，而那一步只收集 sandbox-exec / session-key-mapping / sandbox-toggle，不含本文件；「注入事件」那条 describe 在 Linux 上照跑，只有「真实链路 + 沙箱」这条零覆盖 |
| `task-assets.spec.ts`（部分） | 守卫 | 无条件 `test.skip('标题', fn)` | 低 | 「AI 生成 .docx → 任务资产预览」一条被永久禁用；该文件其余用例在 Linux 上跑 |
| `uninstall-cleanup.spec.ts` | 守卫 | `MIQI_E2E_UNINSTALL=1`（显式 opt-in）+ win32 + `~/.miqi`/`%APPDATA%\miqi-desktop` 当前不存在 + `dist-new/` 下有构建好的安装器；WSL 断言另需 `MIQI_E2E_WSL_ROOTFS` | 高（真机 NSIS 安装/卸载 + WSL 卷清理） | 卸载残留清理只能在真实 Windows 安装上验证：CI 既不设 `MIQI_E2E_UNINSTALL`、job 里也没有构建好的安装器（wsl-e2e 那一步按名字收集的 7 个 spec 不含本文件）；且它会真的安装再卸载整套应用、读写 `%APPDATA%` / `%LOCALAPPDATA%` / `HKCU\Software\MiqroForge` 等真实数据根，只能在被明确告知「本机可跑安装/卸载」的机器上执行（WSL 注销逻辑另有 `src/main/ipc/cleanup.test.ts` 单测覆盖） |

| `sure-integration-gui.spec.ts` | 守卫 | win32 + 本机安装 SURE（`SURE_BIN` 或 `%LOCALAPPDATA%\SURE\bin\sure.exe`）+ 测试项目存在（`MIQI_SURE_PROJECT` 或默认 PoC 路径；均为 spec 内 `existsSync`） | 低（脚本化 mock 模型，单用例约 30s，无真实 LLM） | SURE 是 Windows per-user 安装的本地二进制（v0.1.2），CI runner 均未安装——spec 断言「5 个 `mcp_sure_*` 工具注册 + 审批弹窗 + 报告渲染」，无 sure.exe 时工具根本不注册。主验证在 Python 单测（`tests/execution/test_permission_engine.py`、`test_approval_persistence_e2e.py`）；接进 CI 需在 workflow 里预装 SURE（见 #1256） |

## 已接进 CI（desktop-ci Linux 全量套件，2026-10-15）

复核发现：`QRAFT_LIVE` 在 CI 里**没有任何地方被设置**——这几条 live 用例并入
electron-e2e 之后一直静默 skip（当初「云端登录并入套件」删掉独立流水线
`qraft-cloud-login-live.yml` 与 `playwright.live.config.ts` 时，这个开关没跟着
搬过来）。desktop-ci 的 Linux E2E 步现在注入 `QRAFT_LIVE=1`，直接点亮两条：

- `ai-gateway-live.spec.ts` —— 真实登录 → 默认模型自动就绪（#1172）→ 消息经网关
  真实回复；
- `gateway-creds-live.spec.ts` —— 网关凭据握手 / 缺失可重试 / 重新同步（#1258）。
  顺带修了它第 3 步的竞态：握手文件有「账号工作区 / 认领的存量共享工作区」两个
  候选落点、首次登录同步存在短窗口双写，原来只删「第一个找到的」，可能留下另一份
  使「缺失」判定不成立（本地实测偶发一次）——改为两条落点都清掉再断言。

两者已用真账号本地实跑（`1 passed` / `repeat-each=3` 全绿）。注意 desktop-ci 走
`pull_request_target`：这处 env 改动**合入 develop 后对后续 PR 生效**（与「Install
SURE」同款路径）；macOS job 不注入凭据（选定子集设计、由 Linux 全量覆盖），live
用例在 macOS 上仍 skip。

## 已接进 CI（sure-e2e 工作流，#1278）

`desktop-ci.yml` 走 `pull_request_target`，工作流定义取自 base 分支——PR 内新增
步骤对本 PR 的 CI 是惰性的（评审 #1278 P1-1 实测：SURE E2E 用例全部 skip、job
照样 success）。新增的 `sure-e2e.yml` 由 `pull_request` 触发，工作流取自 PR 分支
本身，并把环境补到「全部用例真跑」：

- `acceptance-page.spec.ts`（5 条：面板三场景 + 模型评估 + 完整修复闭环）——
  模型评估用例要求 `SURE_OPENAI_API_KEY` 与用户级 analysis 配置，由工作流
  注入 / 写入（`~/.config/SURE/sure.yaml`）；完整闭环用例（子代理修完 →
  自动复核 → 发现项「已关闭」）由 `MIQI_E2E_FULL_REPAIR=1` 显式启用，满足
  评审 #1278 P2-2「至少一条用例走到『已完成』分支」——该断言的闭环语义依赖
  钉死的 fork 提交（见下：`c4fd952` 的「目标消失即关闭」+ `88da6bc` 的
  stage 12 契约重建，二者缺一修好的项目都关不掉）；
- `sure-event-bridge.spec.ts`（1 条）——要求 `sure doctor` 列出 miqi：工作流在
  CI 内 clone `JJJY9/SURE`（**钉死 sha** `88da6bc`）并 `cargo build --release`，
  装上 PATH 后就地断言 doctor 能力行（没有 miqi 直接红，钉住「跑的是含集成的
  那份构建」）。

**skip 不再算过**（评审 #1278 P2-1）：工作流跑完读 `test-reports/results.json`，
两条 spec 内出现任何 skipped 即判失败；确实可豁免的用例以
`SURE_E2E_SKIP_ALLOWLIST` 显式点名（默认空）。官方 v0.1.2 的 SURE 安装保留在
electron-e2e（`install-sure: 'true'`，实现已抽进 `.github/actions/e2e-app-setup`，
与 macos-e2e / sure-e2e 共用同一份应用侧准备）——它合入 develop 后对后续 PR 的
electron-e2e 生效（官方构建覆盖验收面板的确定性路径）；**含 miqi 的完整覆盖以
`sure-e2e` 为准**。

## 已接进 CI（#1196）

以下两个 spec 原本也在零覆盖之列（win32-only 守卫），#1196 把它们接进了
`python-tests.yml` 的 wsl-e2e 具名步骤——两者都不依赖 LLM 与 WSL 沙箱
（`issue-1171` 把 `wsl:check` 换成桩结果，`open-external-path-security` 只走
`files.openExternal` IPC），所以这一步不设 `continue-on-error`，要的就是红灯：

- `issue-1171-wsl-platform-guidance.spec.ts`
- `open-external-path-security.spec.ts`

## 本地怎么跑

```bash
cd apps/desktop && npm run build
npx playwright test --config=playwright.config.ts --project=electron <spec>.spec.ts --workers=1
# 上表「门」列的变量按需导出（如 MIQI_RUN_REAL_PDF_E2E=1）；live 用例还需要真实凭据
```
