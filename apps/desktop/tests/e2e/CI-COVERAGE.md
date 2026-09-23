# 零 CI 覆盖清单（#1196）

本文件是 `apps/desktop/tests/e2e/*.spec.ts` 中**至少有一个用例在任何 CI runner 上都不会执行**
的权威清单——它们从不运行、也从不报错，是「e2e 覆盖率」最容易高估的部分（#1196）。

**CI 上真正的执行位置只有三处**：

| 位置 | 平台 | 范围 |
|---|---|---|
| `desktop-ci.yml` → `electron-e2e` | ubuntu | 全量；provider key 写进 app 的 **config.json**（不是环境变量），环境变量只注入 `QRAFT_PHONE` / `QRAFT_PASSWORD`；**不含**任何 `MIQI_RUN_*` / `QRAFT_LIVE` |
| `desktop-ci.yml` → `macos-e2e` | macOS | `--grep-invert` 掉 9 组之后的子集（见 README 覆盖表）；连 `QRAFT_PHONE` / `QRAFT_PASSWORD` 都不注入 |
| `python-tests.yml` → `wsl-e2e` | windows | **按名字**点名 7 个：one-click-install、sandbox-exec、session-key-mapping、sandbox-toggle、#1157、#1171、open-external-path-security |

注意「环境变量」与「config.json」的区别：spec 的守卫读的是 `process.env`。app 能从 config.json
拿到 provider key，不代表 spec 的门也成立——`billing-hosted-live.spec.ts` 把 `DEEPSEEK_API_KEY`
写进了 `HAS_CREDS`，而该变量从未作为环境变量注入任何跑 e2e 的 job，于是它永远跳过（见下表）。

所以「Linux job 是全量」这句话对下表里的 spec 不成立。判罚与维护方式：
`apps/desktop/tests/e2eCiCoverage.test.ts`（`npm test`，quick job）会校验本文件——
**检测到致命守卫却没登记 → 红；登记为「守卫」的行守卫已消失 → 红**。

第二列「判定」：`守卫` = 由上述测试自动检测；`人工` = 守卫形态检测不到、但人工确认过零覆盖。

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
| `full-electron.spec.ts`（部分） | 守卫 | `MIQI_RUN_REAL_WEB_SEARCH_E2E` / `MIQI_RUN_STATEFUL_SESSION_E2E` | 高（真实 LLM） | 5 个用例在 PR CI 上不稳定（#187）：real web search 1、stateful session 1、sidebar switching 2、重启 history 1；该文件其余 11 个用例在 Linux 全量套件里照跑 |
| `ai-gateway-live.spec.ts` | 守卫 | `QRAFT_LIVE=1` | 中（真实平台账号） | opt-in live 用例；CI 从未设置 `QRAFT_LIVE`（连已删除的 cloud-login live workflow 也只跑两个登录 spec） |
| `issue-1185-account-isolation-live.spec.ts` | 守卫 | `QRAFT_LIVE=1` + ≥2 个真实账号 | 中（真实平台账号） | 需要两个账号来回切换验证本地存储隔离 |
| `issue-1185-task-assets-live.spec.ts` | 守卫 | `QRAFT_LIVE=1` | 中（真实平台账号） | 同上：复杂技能产物跨账号切换 |
| `billing-live.spec.ts` | 守卫 | `SLURM_MCP_KEY` | 中（真实网关） | 需要 slurm MCP 网关 key，CI 未注入 |
| `billing-hosted-live.spec.ts` | 人工 | `HAS_CREDS` 里的 `DEEPSEEK_API_KEY` | 中（真实账号 + 真实计费网关） | 该变量只被写进 config.json、从未作为环境变量注入任何跑 e2e 的 job（desktop-ci.yml 的 `DEEPSEEK_API_KEY` 只出现在 heredoc 里），`HAS_CREDS` 恒为 false——守卫看不见它，故标「人工」。要让它在 CI 真跑，需在 `electron-e2e` 注入该变量（live 用例是否每 PR 都花真实额度是产品决策） |
| `mof-synthesis-price-agent.spec.ts` | 守卫 | `MOF_PRICE_PROJECT`（含 extract/、enrich/、report.py 的项目根）+ 私有技能目录 | 高（真实 LLM） | 项目根与 `miqi/skills/mof-synthesis-price-agent` 都是本机私有资产，仓库里没有 |
| `bvse-skill-assets.spec.ts` | 守卫 | `BVSE_SKILL_DIR`（默认 `~/.miqi/skills/bvse-mof-local-ssh`，需含 `.venv`）+ `BVSE_TEST_CIF`（真实 MOF CIF 文件） | 中（本机技能 venv + mock server，长流程） | 需要本机装好的 BVSE 技能依赖与真实 CIF 文件；macOS CI 还因 loopback 到 mock server 不可达额外跳过 |
| `record-bvse-skill.spec.ts` | 守卫 | 同 `bvse-skill-assets`（`RECORD_OUT_DIR` 只是可选输出目录，不是门） | 中（同左，且全程录屏） | 同上：录屏演示真实 BVSE 技能，依赖本机环境 |
| `tool-error-neutral.spec.ts`（部分） | 人工 | `SKIP_SANDBOX_ON_CI`（`MIQI_RUN_SANDBOX_E2E`） | 中（真实 LLM + WSL 沙箱） | 该变量只在 wsl-e2e 的沙箱点名步骤里设置，而那一步不跑本文件；「注入事件」那条 describe 在 Linux 上照跑，只有「真实链路 + 沙箱」这条零覆盖 |
| `task-assets.spec.ts`（部分） | 守卫 | 无条件 `test.skip('标题', fn)` | 低 | 「AI 生成 .docx → 任务资产预览」一条被永久禁用；该文件其余用例在 Linux 上跑 |

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
